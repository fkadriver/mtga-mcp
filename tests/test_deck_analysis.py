"""Tests for deck buildability analysis."""

from __future__ import annotations

import sqlite3

import pytest

from mtga_mcp import db, deck_analysis, decklist


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    c.executemany(
        "INSERT INTO cards(grp_id,name,set_code,collector_number,rarity,type_line) VALUES(?,?,?,?,?,?)",
        [
            (1, "Lightning Bolt", "STA", "42", "rare", "Instant"),
            (2, "Sheoldred, the Apocalypse", "DMU", "107", "mythic", "Legendary Creature"),
            (3, "Counterspell", "STA", "16", "uncommon", "Instant"),
            (4, "Island", "FDN", "270", "basic", "Basic Land — Island"),
        ],
    )
    # Own a full playset of Bolt and 2 Sheoldred; no Counterspell.
    c.executemany("INSERT INTO collection(grp_id,count) VALUES(?,?)", [(1, 4), (2, 2)])
    c.executemany(
        "INSERT INTO wildcards(kind,count) VALUES(?,?)",
        [("common", 5), ("uncommon", 10), ("rare", 3), ("mythic", 3)],
    )
    # Deck A: fully buildable Bo1, decent meta share.
    _store(c, "Aggro", "Standard", best_of=1, meta_share=0.25,
           text="Deck\n4 Lightning Bolt (STA) 42\n2 Sheoldred, the Apocalypse (DMU) 107\n4 Island (FDN) 270\n")
    # Deck B: strong Bo3 tier-1 but missing 4 Counterspell.
    _store(c, "Control", "Standard", best_of=3, tier="1",
           text="Deck\n4 Counterspell (STA) 16\n2 Sheoldred, the Apocalypse (DMU) 107\n")
    # Deck C: fringe Bo1, missing 3 Counterspell.
    _store(c, "Tempo", "Standard", best_of=1, meta_share=0.1,
           text="Deck\n4 Lightning Bolt (STA) 42\n3 Counterspell (STA) 16\n")
    return c


def _store(c, name, fmt, *, best_of=None, tier=None, meta_share=None, text=""):
    decklist.store_deck(
        c, name=name, fmt=fmt, cards=decklist.parse_decklist(text),
        source="text", best_of=best_of, tier=tier, meta_share=meta_share,
    )


def test_gap_buildable_deck(conn):
    gap = deck_analysis.deck_gap(conn, "Aggro")
    assert gap["buildable"] is True
    assert gap["missing_cards"] == []
    assert gap["wildcards_needed"] == {}


def test_gap_missing_cards_and_wildcards(conn):
    gap = deck_analysis.deck_gap(conn, "Control")
    names = {m["name"]: m for m in gap["missing_cards"]}
    assert names["Counterspell"]["missing"] == 4
    assert "Sheoldred, the Apocalypse" not in names  # owned 2, needed 2
    assert gap["wildcards_needed"] == {"uncommon": 4}
    assert gap["buildable"] is False


def test_gap_excludes_basics(conn):
    gap = deck_analysis.deck_gap(conn, "Aggro")
    assert all(m["name"] != "Island" for m in gap["missing_cards"])


def test_gap_caps_at_playset(conn):
    # Tempo runs 3 Counterspell -> needs exactly 3, not more.
    gap = deck_analysis.deck_gap(conn, "Tempo")
    cs = next(m for m in gap["missing_cards"] if m["name"] == "Counterspell")
    assert cs["missing"] == 3


def test_best_buildable_ranks_by_strength_times_buildability(conn):
    ranked = deck_analysis.best_buildable_deck(conn)
    order = [r["name"] for r in ranked]
    # Aggro (0.25*1.0) > Control (1.0*0.2) > Tempo (0.1*0.25)
    assert order == ["Aggro", "Control", "Tempo"]
    assert ranked[0]["buildable_now"] is True


def test_best_buildable_filters_best_of(conn):
    bo3 = [r["name"] for r in deck_analysis.best_buildable_deck(conn, best_of=3)]
    assert bo3 == ["Control"]
    bo1 = {r["name"] for r in deck_analysis.best_buildable_deck(conn, best_of=1)}
    assert bo1 == {"Aggro", "Tempo"}


def test_best_buildable_max_wildcards(conn):
    only_free = deck_analysis.best_buildable_deck(conn, max_wildcards=0)
    assert [r["name"] for r in only_free] == ["Aggro"]


def test_craft_priority_unlocks_most_decks(conn):
    ranked = deck_analysis.craft_priority(conn)
    top = ranked[0]
    assert top["name"] == "Counterspell"
    assert top["rarity"] == "uncommon"
    assert top["decks_needing"] == 2
    assert top["decks_completed_if_crafted"] == 2
    assert top["copies_needed"] == 7  # 4 (Control) + 3 (Tempo)
