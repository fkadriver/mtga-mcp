"""Parse wildcard balances (and, if present, owned cards) out of MTGA's Player.log.

MTGA writes these payloads only when the "Detailed Logs (Plugin Support)" setting is
enabled (Settings -> Account). Modern clients log:

  * ``InventoryInfo`` — wildcard and currency balances (WildCardCommons, Gems, Gold, ...).

Older clients additionally logged the full owned-card collection:

  * ``PlayerInventory.GetPlayerCardsV3`` — a JSON object mapping GrpId -> owned count
  * ``PlayerInventory.GetPlayerInventory`` — the legacy wildcard/currency payload (wcCommon...)

As of 2026 the native client no longer emits the full owned-card dump, so `cards_written`
is commonly 0 even with detailed logs on; wildcards still import. We scan for the *last*
occurrence of each marker (most recent state) and brace-match the JSON object that follows.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db, paths

# Modern InventoryInfo schema: field -> our wildcards.kind label.
_INVENTORY_FIELDS = {
    "WildCardCommons": "common",
    "WildCardUnCommons": "uncommon",
    "WildCardRares": "rare",
    "WildCardMythics": "mythic",
    "Gold": "gold",
    "Gems": "gems",
    "TotalVaultProgress": "vault",
}

# Legacy PlayerInventory.GetPlayerInventory schema, kept as a fallback.
_LEGACY_INVENTORY_FIELDS = {
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
    owned_cards_available: bool = False  # did the log contain a full owned-card dump?


def _extract_all_json_objects(text: str, marker: str) -> list[dict]:
    """Return every JSON object following an occurrence of `marker`, in order."""
    results: list[dict] = []
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
            results.append(obj)
    return results


def _extract_last_json_object(text: str, marker: str) -> dict | None:
    """Return the JSON object following the last occurrence of `marker`, or None."""
    objects = _extract_all_json_objects(text, marker)
    return objects[-1] if objects else None


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

    # Prefer the modern InventoryInfo payload; fall back to the legacy schema.
    inventory = _extract_last_json_object(text, '"InventoryInfo"')
    inventory_fields = _INVENTORY_FIELDS
    if inventory is None:
        inventory = _extract_last_json_object(text, "PlayerInventory.GetPlayerInventory")
        inventory_fields = _LEGACY_INVENTORY_FIELDS

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
            for field, kind in inventory_fields.items():
                if field in inventory and isinstance(inventory[field], (int, float)):
                    conn.execute(
                        "INSERT INTO wildcards(kind, count) VALUES(?, ?)",
                        (kind, int(inventory[field])),
                    )
                    wildcards_written += 1
        # `collection_source` describes the *owned cards*, so only stamp it when we actually
        # wrote owned cards -- otherwise a wildcard-only import (the norm on modern clients,
        # which don't log owned cards) would clobber the provenance of a full memory-scanner
        # collection (see ingest_export). Wildcard provenance is tracked separately.
        if cards:
            db.set_meta(conn, "collection_source", source or "unknown")
        if inventory:
            db.set_meta(conn, "wildcards_source", source or "unknown")

    return CollectionResult(
        cards_written,
        wildcards_written,
        source if (cards or inventory) else None,
        owned_cards_available=bool(cards),
    )
