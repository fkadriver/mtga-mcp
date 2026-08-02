"""Tests for queries.py against a small in-memory fixture DB."""

from __future__ import annotations

import sqlite3

import pytest

from mtga_mcp import db, queries


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    # A handful of cards across sets/rarities/colors.
    cards = [
        # grp_id, name, set, cn, rarity, colors, type_line
        (1, "Lightning Bolt", "STA", "42", "rare", "R", "Instant"),
        (2, "Island", "FDN", "270", "basic", "U", "Basic Land — Island"),
        (3, "Shivan Dragon", "FDN", "150", "rare", "R", "Creature — Dragon"),
        (4, "Counterspell", "FDN", "50", "uncommon", "U", "Instant"),
        (5, "Niv-Mizzet", "GRN", "192", "mythic", "UR", "Legendary Creature — Dragon"),
    ]
    c.executemany(
        "INSERT INTO cards(grp_id,name,set_code,collector_number,rarity,colors,type_line) "
        "VALUES(?,?,?,?,?,?,?)",
        cards,
    )
    # Player owns some of them.
    c.executemany(
        "INSERT INTO collection(grp_id,count) VALUES(?,?)",
        [(1, 4), (3, 1), (5, 2)],
    )
    c.executemany(
        "INSERT INTO wildcards(kind,count) VALUES(?,?)",
        [("rare", 7), ("mythic", 3)],
    )
    c.commit()
    return c


def test_search_by_name(conn):
    rows = queries.search_cards(conn, name="bolt")
    assert [r["name"] for r in rows] == ["Lightning Bolt"]
    assert rows[0]["owned"] == 4


def test_search_by_color_and_rarity(conn):
    rows = queries.search_cards(conn, colors="R", rarity="rare")
    assert {r["name"] for r in rows} == {"Lightning Bolt", "Shivan Dragon"}


def test_multicolor_requires_all_letters(conn):
    # "UR" should match Niv-Mizzet (colors 'UR') but not mono-color cards.
    rows = queries.search_cards(conn, colors="UR")
    assert [r["name"] for r in rows] == ["Niv-Mizzet"]


def test_owned_only(conn):
    owned = {r["name"] for r in queries.owned_cards(conn)}
    assert owned == {"Lightning Bolt", "Shivan Dragon", "Niv-Mizzet"}
    assert "Counterspell" not in owned


def test_missing_from_set_excludes_basics_and_completed(conn):
    rows = queries.missing_from_set(conn, "FDN")
    names = {r["name"]: r["needed"] for r in rows}
    assert "Island" not in names  # basics excluded
    assert names["Shivan Dragon"] == 3  # owns 1, needs 3 more
    assert names["Counterspell"] == 4  # owns 0
    # Lightning Bolt is in STA and fully owned -> not in FDN missing list
    assert "Lightning Bolt" not in names


def test_collection_summary(conn):
    s = queries.collection_summary(conn)
    assert s["distinct_owned"] == 3
    assert s["total_copies"] == 7  # 4 + 1 + 2
    assert s["wildcards"] == {"rare": 7, "mythic": 3}


def test_readonly_sql_allows_select(conn):
    rows = queries.run_readonly_sql(conn, "SELECT COUNT(*) AS n FROM cards")
    assert rows == [{"n": 5}]


@pytest.mark.parametrize("bad", ["DROP TABLE cards", "SELECT 1; DELETE FROM cards", "UPDATE cards SET name='x'"])
def test_readonly_sql_rejects_non_select(conn, bad):
    with pytest.raises(ValueError):
        queries.run_readonly_sql(conn, bad)
