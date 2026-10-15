"""Import your own MTGA decks -- the ones you've built in Arena -- straight from Player.log.

The client periodically logs its full deck cache in two matching pieces:

  * ``"DeckSummaries": [ {"DeckIdInternal": <id>, "Name": <name>,
    "Attributes": [{"name": "Format", "value": <fmt>}, ...]}, ... ]`` -- metadata only, no
    cards, one entry per deck you've ever built (precons included).
  * ``"DecksInternal": { <id>: {"MainDeck": [{"cardId": <grp_id>, "quantity": <n>}, ...],
    "Sideboard": [...]}, ... }`` -- the actual card lists, keyed by the same id.

Joining the two on ``DeckIdInternal`` gives every deck you own, in full, with no name
matching needed at all -- ``cardId`` *is* the MTGA GrpId (our ``cards.grp_id``), so each card
resolves by an exact id lookup rather than ``resolve_card``'s name-based path.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from . import decklist, paths
from .decklist import DeckCard
from .ingest_collection import _match_object


@dataclass
class MyDecksResult:
    imported: int
    skipped: int
    unresolved_cards: dict[str, list[int]]  # deck name -> cardIds with no catalog match


def _match_array(text: str, open_idx: int) -> str | None:
    """Like ingest_collection._match_object, but for a `[...]` array."""
    depth = 0
    in_str = False
    escape = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
    return None


def _latest(text: str, marker: str, open_char: str):
    """The LAST (most current) `marker`'s value in `text` -- a `{...}` or `[...]`."""
    idx = text.rfind(f'"{marker}"')
    if idx == -1:
        return None
    pos = text.find(open_char, idx)
    if pos == -1:
        return None
    if open_char == "[":
        raw = _match_array(text, pos)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return _match_object(text, pos)  # already parsed


def _deck_format(summary: dict) -> str | None:
    return next(
        (a.get("value") for a in summary.get("Attributes", [])
         if isinstance(a, dict) and a.get("name") == "Format"),
        None,
    )


def import_my_decks(conn: sqlite3.Connection, *, name_filter: str | None = None) -> MyDecksResult:
    """Import every deck found in the newest Player.log's DeckSummaries/DecksInternal dump.

    Skips decks already imported (matched by the synthetic ``source_url`` ``playerlog:<id>``),
    so re-running after building new decks only adds the new ones. ``name_filter`` restricts
    to deck names containing that substring (case-insensitive), e.g. to re-run for one deck
    after editing it in Arena (its old row is left as-is; you'd `deck delete` first).
    """
    files = paths.detailed_log_files()
    text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in files if p.exists()
    )
    summaries = _latest(text, "DeckSummaries", "[") or []
    internals = _latest(text, "DecksInternal", "{") or {}

    imported = skipped = 0
    unresolved: dict[str, list[int]] = {}
    for summary in summaries:
        deck_id = summary.get("DeckIdInternal")
        name = summary.get("Name")
        if not deck_id or not name or deck_id not in internals:
            continue
        if name_filter and name_filter.lower() not in name.lower():
            continue
        source_url = f"playerlog:{deck_id}"
        exists = conn.execute(
            "SELECT 1 FROM decks WHERE source_url = ?", (source_url,)
        ).fetchone()
        if exists:
            skipped += 1
            continue

        deck_cards: list[DeckCard] = []
        bad_ids: list[int] = []
        board_keys = (("MainDeck", "main"), ("Sideboard", "side"))
        for src_key, board in board_keys:
            for entry in internals[deck_id].get(src_key, []):
                grp_id, qty = entry.get("cardId"), entry.get("quantity")
                if not isinstance(grp_id, int) or not isinstance(qty, int):
                    continue
                row = conn.execute(
                    "SELECT name, set_code FROM cards WHERE grp_id = ?", (grp_id,)
                ).fetchone()
                if row is None:
                    bad_ids.append(grp_id)
                    continue
                deck_cards.append(DeckCard(quantity=qty, name=row["name"],
                                            set_code=row["set_code"], board=board))
        if not deck_cards:
            continue
        if bad_ids:
            unresolved[name] = bad_ids

        decklist.store_deck(
            conn, name=name, fmt=_deck_format(summary), cards=deck_cards,
            source="player_log", source_url=source_url,
        )
        imported += 1

    return MyDecksResult(imported=imported, skipped=skipped, unresolved_cards=unresolved)
