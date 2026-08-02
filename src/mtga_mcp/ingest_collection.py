"""Parse owned cards and wildcard balances out of MTGA's Player.log.

MTGA writes these payloads only when the "Detailed Logs (Plugin Support)" setting is
enabled (Settings -> Account). When enabled and the Collection screen has been opened,
the log contains:

  * ``PlayerInventory.GetPlayerCardsV3`` followed by a JSON object mapping GrpId -> count
  * ``PlayerInventory.GetPlayerInventory`` / ``PlayerInventory`` with wildcard/currency counts

We scan for the *last* occurrence of each marker (most recent state) and brace-match the
JSON object that follows. If no marker is present we return zero counts and a hint, rather
than failing, so a catalog-only setup still works.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db, paths

# Marker -> our wildcards.kind label, for the PlayerInventory payload.
_WILDCARD_FIELDS = {
    "wcCommon": "common",
    "wcUncommon": "uncommon",
    "wcRare": "rare",
    "wcMythic": "mythic",
    "gold": "gold",
    "gems": "gems",
    "vaultProgress": "vault",
}


@dataclass
class CollectionResult:
    cards_written: int
    wildcards_written: int
    source: str | None  # which log file the data came from, or None if not found


def _extract_last_json_object(text: str, marker: str) -> dict | None:
    """Return the JSON object following the last occurrence of `marker`, or None."""
    result: dict | None = None
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
            result = obj  # keep the latest
    return result


def _match_object(text: str, open_idx: int) -> dict | None:
    """Brace-match a JSON object starting at `open_idx`; return parsed dict or None."""
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
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[open_idx : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _read_log() -> tuple[str, str | None]:
    """Return (combined log text, source description). Prefers current then prev log."""
    parts: list[str] = []
    source: str | None = None
    for p in (paths.PLAYER_LOG, paths.PLAYER_LOG_PREV):
        if Path(p).exists():
            parts.append(Path(p).read_text(encoding="utf-8", errors="replace"))
            source = source or p.name
    return "\n".join(parts), source


def ingest(conn: sqlite3.Connection) -> CollectionResult:
    text, source = _read_log()
    if not text:
        return CollectionResult(0, 0, None)

    cards = _extract_last_json_object(text, "PlayerInventory.GetPlayerCardsV3")
    inventory = _extract_last_json_object(text, "PlayerInventory.GetPlayerInventory")

    cards_written = 0
    wildcards_written = 0
    with conn:
        if cards:
            conn.execute("DELETE FROM collection")
            for grp, count in cards.items():
                if str(grp).isdigit() and isinstance(count, int):
                    conn.execute(
                        "INSERT INTO collection(grp_id, count) VALUES(?, ?)",
                        (int(grp), count),
                    )
                    cards_written += 1
        if inventory:
            conn.execute("DELETE FROM wildcards")
            for field, kind in _WILDCARD_FIELDS.items():
                if field in inventory and isinstance(inventory[field], (int, float)):
                    conn.execute(
                        "INSERT INTO wildcards(kind, count) VALUES(?, ?)",
                        (kind, int(inventory[field])),
                    )
                    wildcards_written += 1
        if cards or inventory:
            db.set_meta(conn, "collection_source", source or "unknown")

    return CollectionResult(cards_written, wildcards_written, source if (cards or inventory) else None)
