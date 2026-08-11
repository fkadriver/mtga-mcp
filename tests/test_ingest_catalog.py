"""Tests for loading MTGA's card catalog, focusing on name de-markup.

MTGA's `Formatted = 1` display names carry UI markup for ~5% of cards (`<nobr>` around
hyphenated names, a `<sprite ...>` prefix on Alchemy rebalanced cards). A clean `Formatted = 0`
variant exists only for those cards, so ingest prefers it and falls back to `Formatted = 1`.
"""

from __future__ import annotations

import sqlite3

from mtga_mcp import db, ingest_catalog


def _make_source(path) -> None:
    """Write a minimal MTGA-card-database-shaped SQLite file at `path`."""
    src = sqlite3.connect(path)
    src.executescript(
        """
        CREATE TABLE Cards (
            GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT,
            Rarity INTEGER, Colors TEXT, Power TEXT, Toughness TEXT,
            IsPrimaryCard INTEGER, IsToken INTEGER
        );
        CREATE TABLE Localizations_enUS (LocId INTEGER, Loc TEXT, Formatted INTEGER);
        """
    )
    # (GrpId, TitleId, set, cn, rarity, colors, pow, tou, primary, token)
    src.executemany(
        "INSERT INTO Cards VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (100, 1, "LTR", "1", 4, "3", None, None, 1, 0),   # <nobr> markup card
            (200, 2, "VOW", "2", 5, "2", None, None, 1, 0),   # Alchemy <sprite> card
            (300, 3, "DMU", "3", 2, "4", "2", "2", 1, 0),     # plain, Formatted=1 only
            (400, 4, "LTR", "4", 0, "",  None, None, 0, 1),   # token / non-primary -> excluded
        ],
    )
    src.executemany(
        "INSERT INTO Localizations_enUS VALUES (?,?,?)",
        [
            (1, "<nobr>Barad-dûr</nobr>", 1), (1, "Barad-dûr", 0),
            (2, '<sprite="SpriteSheet_MiscIcons" name="arena_a">The Meathook Massacre', 1),
            (2, "A-The Meathook Massacre", 0),
            (3, "Llanowar Elves", 1),                          # no Formatted=0 -> fallback
            (4, "Some Token", 1),
        ],
    )
    src.commit()
    src.close()


def test_catalog_prefers_clean_name_and_falls_back(tmp_path, monkeypatch):
    source = tmp_path / "Raw_CardDatabase.mtga"
    _make_source(str(source))
    monkeypatch.setattr(ingest_catalog.paths, "find_card_database", lambda: source)

    conn = db.connect(":memory:")
    written = ingest_catalog.ingest(conn)

    names = dict(conn.execute("SELECT grp_id, name FROM cards").fetchall())
    assert written == 3                       # token/non-primary excluded
    assert names[100] == "Barad-dûr"          # <nobr> stripped via Formatted=0
    assert names[200] == "A-The Meathook Massacre"  # Alchemy prefix as plain A-
    assert names[300] == "Llanowar Elves"     # fell back to Formatted=1
    assert not any("<" in n for n in names.values())


def test_catalog_maps_rarity_and_colors(tmp_path, monkeypatch):
    source = tmp_path / "Raw_CardDatabase.mtga"
    _make_source(str(source))
    monkeypatch.setattr(ingest_catalog.paths, "find_card_database", lambda: source)

    conn = db.connect(":memory:")
    ingest_catalog.ingest(conn)
    row = conn.execute("SELECT rarity, colors FROM cards WHERE grp_id = 200").fetchone()
    assert row["rarity"] == "mythic" and row["colors"] == "U"
