"""Tests for decklist parsing and card resolution."""

from __future__ import annotations

import sqlite3

import pytest

from mtga_mcp import db, decklist


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    # Two printings of Lightning Bolt at different rarities; a basic; a DFC.
    c.executemany(
        "INSERT INTO cards(grp_id,name,set_code,collector_number,rarity,type_line) VALUES(?,?,?,?,?,?)",
        [
            (1, "Lightning Bolt", "STA", "42", "rare", "Instant"),
            (2, "Lightning Bolt", "M10", "146", "common", "Instant"),
            (3, "Island", "FDN", "270", "basic", "Basic Land — Island"),
            (4, "Sheoldred, the Apocalypse", "DMU", "107", "mythic", "Legendary Creature"),
            (5, "Fable of the Mirror-Breaker", "NEO", "141", "rare",
             "Enchantment — Saga // Creature"),
        ],
    )
    c.executemany(
        "INSERT INTO collection(grp_id,count) VALUES(?,?)",
        [(1, 1), (2, 2), (4, 1)],  # 3 Bolt across printings, 1 Sheoldred
    )
    c.commit()
    return c


ARENA = """Deck
4 Lightning Bolt (STA) 42
2 Sheoldred, the Apocalypse (DMU) 107
8 Island (FDN) 270

Sideboard
2 Lightning Bolt (M10) 146
"""

MTGO = """4 Lightning Bolt
2 Sheoldred, the Apocalypse

3 Island
"""


def test_parse_arena_with_sections():
    cards = decklist.parse_decklist(ARENA)
    main = {(c.name, c.quantity) for c in cards if c.board == "main"}
    side = {(c.name, c.quantity) for c in cards if c.board == "side"}
    assert ("Lightning Bolt", 4) in main
    assert ("Island", 8) in main
    assert ("Lightning Bolt", 2) in side


def test_parse_mtgo_blank_line_starts_sideboard():
    cards = decklist.parse_decklist(MTGO)
    boards = {c.name: c.board for c in cards}
    assert boards["Lightning Bolt"] == "main"
    assert boards["Island"] == "side"  # after the blank line


def test_resolve_pools_ownership_across_printings():
    c = _seeded()
    info = decklist.resolve_card(c, "Lightning Bolt")
    assert info.matched
    assert info.owned == 3  # 1 (STA) + 2 (M10)
    assert info.rarity == "common"  # cheapest printing decides wildcard cost
    assert info.is_basic is False


def test_resolve_basic_land():
    info = decklist.resolve_card(_seeded(), "Island")
    assert info.is_basic is True


def test_resolve_dfc_front_face_and_arena_prefix():
    c = _seeded()
    assert decklist.resolve_card(c, "Fable of the Mirror-Breaker").matched
    # Rebalanced "A-" prefix strips to the base name.
    assert decklist.resolve_card(c, "A-Sheoldred, the Apocalypse").matched


def test_resolve_unknown_card():
    assert decklist.resolve_card(_seeded(), "Not A Real Card").matched is False


def test_store_deck_reports_unresolved():
    c = _seeded()
    cards = decklist.parse_decklist("Deck\n4 Lightning Bolt (STA) 42\n2 Fake Card\n")
    stored = decklist.store_deck(c, name="T", fmt="Standard", cards=cards, source="text")
    assert stored.resolved == 1
    assert stored.unresolved == ["Fake Card"]
    assert c.execute("SELECT COUNT(*) FROM deck_cards WHERE deck_id=?", (stored.deck_id,)).fetchone()[0] == 2


def _seeded() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    c.executemany(
        "INSERT INTO cards(grp_id,name,set_code,collector_number,rarity,type_line) VALUES(?,?,?,?,?,?)",
        [
            (1, "Lightning Bolt", "STA", "42", "rare", "Instant"),
            (2, "Lightning Bolt", "M10", "146", "common", "Instant"),
            (3, "Island", "FDN", "270", "basic", "Basic Land — Island"),
            (4, "Sheoldred, the Apocalypse", "DMU", "107", "mythic", "Legendary Creature"),
            (5, "Fable of the Mirror-Breaker", "NEO", "141", "rare", "Enchantment — Saga // Creature"),
        ],
    )
    c.executemany("INSERT INTO collection(grp_id,count) VALUES(?,?)", [(1, 1), (2, 2), (4, 1)])
    c.commit()
    return c
