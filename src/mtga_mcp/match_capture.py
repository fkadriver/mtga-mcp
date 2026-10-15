"""Capture your own completed-match results (win/loss + which deck) out of Player.log.

MTGA logs a self-contained ``matchGameRoomStateChangedEvent`` when a match completes
(``stateType: "MatchGameRoomStateType_MatchCompleted"``): it carries both ``gameRoomConfig
.reservedPlayers`` (each player's ``playerName``/``teamId``/``eventId``, e.g. "Play") and
``finalMatchResult.resultList`` (``winningTeamId`` per scope -- we use ``MatchScope_Match``,
not ``MatchScope_Game``, since a Bo3 match has one of the latter per game but only one
match-level result). We figure out which player is "us" by matching ``playerName`` against
the account's own ``screenName`` (from the earlier ``AuthenticateResponse``), then compare
``winningTeamId`` to our ``teamId``.

The completed-match event doesn't say which deck was used. The ``EventSetDeckV3`` client
request logged shortly before queueing does: ``{"request": "{\\"EventName\\": <queue>,
\\"Summary\\": {\\"Name\\": <deck name>, \\"Attributes\\": [{\\"name\\":\\"Format\\", ...}]}}"}``
(the whole thing double-JSON-encoded -- MTGA logs its own request body as a JSON *string*).
We take the most recent one before the match, matched on queue id (``EventName`` ==
``eventId``), and use its deck name to join against `decks.name` (case-insensitive) --
there's no real foreign key between an Arena deck and a stored `decks` row, so this only
lines up when you've named your in-game deck the same as something you imported.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from . import db, paths
from .ingest_collection import _match_object

_MTIME_KEY = "match_capture_last_log_mtime"
_SCREEN_NAME_RE = re.compile(r'"screenName"\s*:\s*"([^"]+)"')


@dataclass
class MatchCaptureResult:
    new_matches: int
    total_matches: int
    skipped: bool = False  # logs unchanged since last run -> parsing skipped


def _positioned_objects(text: str, marker: str) -> list[tuple[int, dict]]:
    """Like ingest_collection._extract_all_json_objects, but keeping each object's position
    in `text` so callers can order events (e.g. "the deck-select nearest before this match")."""
    results: list[tuple[int, dict]] = []
    start = 0
    while True:
        idx = text.find(marker, start)
        if idx == -1:
            break
        start = idx + len(marker)
        brace = text.find("{", start)
        if brace == -1:
            continue
        obj = _match_object(text, brace)
        if isinstance(obj, dict):
            results.append((idx, obj))
    return results


def _deck_selections(text: str) -> list[tuple[int, str, str, str | None]]:
    """(position, queue_event_name, deck_name, format) for each EventSetDeckV3 request."""
    selections: list[tuple[int, str, str, str | None]] = []
    for pos, outer in _positioned_objects(text, "EventSetDeckV3"):
        raw_request = outer.get("request")
        if not isinstance(raw_request, str):
            continue
        try:
            inner = json.loads(raw_request)
        except ValueError:
            continue
        event_name = inner.get("EventName")
        summary = inner.get("Summary") or {}
        name = summary.get("Name")
        if not isinstance(event_name, str) or not isinstance(name, str):
            continue
        fmt = next(
            (a.get("value") for a in summary.get("Attributes", [])
             if isinstance(a, dict) and a.get("name") == "Format"),
            None,
        )
        selections.append((pos, event_name, name, fmt))
    return selections


def _completed_matches(text: str) -> list[tuple[int, dict]]:
    """(position, gameRoomInfo) for each completed-match event. ``stateType``,
    ``gameRoomConfig`` and ``finalMatchResult`` all live inside ``gameRoomInfo`` -- the top
    level object brace-matched off the marker is just ``{"gameRoomInfo": {...}}``."""
    out = []
    for pos, obj in _positioned_objects(text, "matchGameRoomStateChangedEvent"):
        info = obj.get("gameRoomInfo")
        if isinstance(info, dict) and info.get("stateType") == "MatchGameRoomStateType_MatchCompleted":
            out.append((pos, info))
    return out


def _parse_file(text: str) -> list[dict]:
    """Every resolvable completed match in `text`: {match_id, deck_name, format, won}."""
    screen_match = _SCREEN_NAME_RE.search(text)
    screen_name = screen_match.group(1) if screen_match else None
    if screen_name is None:
        return []
    deck_selections = _deck_selections(text)

    parsed: list[dict] = []
    for pos, info in _completed_matches(text):
        config = info.get("gameRoomConfig") or {}
        final_result = info.get("finalMatchResult") or {}
        match_id = config.get("matchId") or final_result.get("matchId")
        reserved = config.get("reservedPlayers")
        if not match_id or not isinstance(reserved, list):
            continue
        me = next((p for p in reserved if p.get("playerName") == screen_name), None)
        if me is None or "teamId" not in me:
            continue
        result_list = final_result.get("resultList") or []
        match_result = next(
            (r for r in result_list if r.get("scope") == "MatchScope_Match"), None
        )
        if match_result is None or "winningTeamId" not in match_result:
            continue
        won = int(match_result["winningTeamId"] == me["teamId"])

        # The nearest deck-select before this match, queued for the same event ("Play", ...).
        our_event = me.get("eventId")
        deck_name = fmt = None
        for sel_pos, event_name, name, selection_fmt in reversed(deck_selections):
            if sel_pos < pos and event_name == our_event:
                deck_name, fmt = name, selection_fmt
                break

        parsed.append({"match_id": match_id, "deck_name": deck_name, "format": fmt, "won": won})
    return parsed


def capture_matches(conn: sqlite3.Connection, *, force: bool = False) -> MatchCaptureResult:
    files = paths.detailed_log_files()
    latest_mtime = max((f.stat().st_mtime for f in files), default=0.0)
    last_seen = db.get_meta(conn, _MTIME_KEY)
    total_matches = conn.execute("SELECT COUNT(*) FROM match_history").fetchone()[0]
    if not force and last_seen is not None and latest_mtime <= float(last_seen):
        return MatchCaptureResult(new_matches=0, total_matches=total_matches, skipped=True)

    now = datetime.now(timezone.utc).isoformat()
    new_matches = 0
    with conn:
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _parse_file(text):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO match_history"
                    "(match_id, deck_name, format, won, played_at) VALUES (?,?,?,?,?)",
                    (m["match_id"], m["deck_name"], m["format"], m["won"], now),
                )
                new_matches += cur.rowcount
        total_matches = conn.execute("SELECT COUNT(*) FROM match_history").fetchone()[0]
        db.set_meta(conn, _MTIME_KEY, repr(latest_mtime))

    return MatchCaptureResult(new_matches=new_matches, total_matches=total_matches)
