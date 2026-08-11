"""Load MTGA's bundled card catalog into our `cards` table.

MTGA ships a read-only SQLite database. Card display names live in a separate
`Localizations_enUS` table keyed by `TitleId`. We read primary, non-token cards and
upsert the columns MTGA knows about (name, set, collector number, rarity, colors,
power/toughness). Richer fields (oracle text, mana cost, prices, legalities) are filled
in later by ingest_scryfall using the shared GrpId == arena_id key.

Names come in three `Formatted` variants per card: `Formatted = 1` is the canonical name
present for every card, but ~5% carry UI markup -- `<nobr>Barad-dûr</nobr>` for hyphenated
names, and a `<sprite="SpriteSheet_MiscIcons" name="arena_a">` prefix marking Alchemy
rebalanced cards. `Formatted = 0` is a clean, unformatted variant that exists *only* for
those marked-up cards (and renders the Alchemy prefix as a plain `A-`, matching how Arena and
deck sites reference rebalanced cards). We therefore prefer `Formatted = 0` and fall back to
`Formatted = 1`, so `cards.name` stays plain text that deck-list resolution can match.
"""

from __future__ import annotations

import sqlite3

from . import db, paths

# MTGA rarity enum -> label.
_RARITY = {0: "token", 1: "basic", 2: "common", 3: "uncommon", 4: "rare", 5: "mythic"}
# MTGA color enum -> WUBRG letter.
_COLOR = {"1": "W", "2": "U", "3": "B", "4": "R", "5": "G"}

_CATALOG_QUERY = """
SELECT c.GrpId, COALESCE(plain.Loc, fmt.Loc) AS name, c.ExpansionCode, c.CollectorNumber,
       c.Rarity, c.Colors, c.Power, c.Toughness
FROM Cards c
JOIN Localizations_enUS fmt ON c.TitleId = fmt.LocId AND fmt.Formatted = 1
LEFT JOIN Localizations_enUS plain ON c.TitleId = plain.LocId AND plain.Formatted = 0
       AND plain.Loc IS NOT NULL AND plain.Loc != ''
WHERE c.IsPrimaryCard = 1 AND c.IsToken = 0 AND fmt.Loc IS NOT NULL AND fmt.Loc != ''
"""


def _decode_colors(raw: str | None) -> str:
    if not raw:
        return ""
    return "".join(_COLOR.get(part.strip(), "") for part in raw.split(","))


def ingest(conn: sqlite3.Connection) -> int:
    """Populate `cards` from the newest MTGA card database. Returns rows written."""
    src_path = paths.find_card_database()
    src = sqlite3.connect(f"file:{src_path}?mode=ro&immutable=1", uri=True)
    src.row_factory = sqlite3.Row
    try:
        rows = src.execute(_CATALOG_QUERY).fetchall()
    finally:
        src.close()

    written = 0
    with conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO cards (grp_id, name, set_code, collector_number, rarity,
                                   colors, power, toughness)
                VALUES (:grp_id, :name, :set_code, :collector_number, :rarity,
                        :colors, :power, :toughness)
                ON CONFLICT(grp_id) DO UPDATE SET
                    name = excluded.name,
                    set_code = excluded.set_code,
                    collector_number = excluded.collector_number,
                    rarity = excluded.rarity,
                    colors = excluded.colors,
                    power = excluded.power,
                    toughness = excluded.toughness
                """,
                {
                    "grp_id": r["GrpId"],
                    "name": r["name"],
                    "set_code": r["ExpansionCode"],
                    "collector_number": r["CollectorNumber"],
                    "rarity": _RARITY.get(r["Rarity"], "unknown"),
                    "colors": _decode_colors(r["Colors"]),
                    "power": r["Power"] or None,
                    "toughness": r["Toughness"] or None,
                },
            )
            written += 1
        db.set_meta(conn, "catalog_source", src_path.name)
    return written
