"""Tests for near-duplicate deck detection (deck_dedupe.py)."""

from __future__ import annotations

import sqlite3

import pytest

from mtga_mcp import db, deck_dedupe, decklist


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    c.executemany(
        "INSERT INTO cards(grp_id, name, set_code, rarity) VALUES (?,?,?,?)",
        [
            (1, "Lightning Bolt", "STA", "rare"),
            (2, "Counterspell", "STA", "uncommon"),
            (3, "Shock", "FDN", "common"),
            (4, "Island", "FDN", "basic"),
            (5, "Opt", "STA", "common"),
        ],
    )
    c.commit()
    return c


def _store(c, name, fmt, text, source="text"):
    return decklist.store_deck(
        c, name=name, fmt=fmt, cards=decklist.parse_decklist(text), source=source,
    )


def test_identical_mainboards_are_linked(conn):
    a = _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    b = _store(conn, "B", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    canonical = deck_dedupe.link_duplicate(conn, b.deck_id)
    assert canonical == a.deck_id


def test_two_card_difference_is_still_linked(conn):
    a = _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    # One copy of Counterspell swapped for one copy of Shock -> |4-3| + |0-1| = 2 total.
    b = _store(conn, "B", "Standard",
               "Deck\n4 Lightning Bolt (STA) 1\n3 Counterspell (STA) 2\n1 Shock (FDN) 3\n")
    canonical = deck_dedupe.link_duplicate(conn, b.deck_id)
    assert canonical == a.deck_id


def test_more_than_two_card_difference_is_not_linked(conn):
    a = _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    # Two copies of Counterspell swapped for two of Shock -> |4-2| + |0-2| = 4 total.
    b = _store(conn, "B", "Standard",
               "Deck\n4 Lightning Bolt (STA) 1\n2 Counterspell (STA) 2\n2 Shock (FDN) 3\n")
    canonical = deck_dedupe.link_duplicate(conn, b.deck_id)
    assert canonical is None


def test_different_formats_never_link(conn):
    a = _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    b = _store(conn, "B", "Historic", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    canonical = deck_dedupe.link_duplicate(conn, b.deck_id)
    assert canonical is None


def test_basics_are_ignored_in_the_comparison(conn):
    a = _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n20 Island (FDN) 4\n")
    b = _store(conn, "B", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n17 Island (FDN) 4\n")
    canonical = deck_dedupe.link_duplicate(conn, b.deck_id)
    assert canonical == a.deck_id


def test_sideboard_is_ignored(conn):
    a = _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    b = _store(conn, "B", "Standard",
               "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n\n"
               "Sideboard\n4 Opt (STA) 5\n")
    canonical = deck_dedupe.link_duplicate(conn, b.deck_id)
    assert canonical == a.deck_id


def test_backfill_chains_to_the_earliest_deck_in_the_cluster(conn):
    a = _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    b = _store(conn, "B", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    c = _store(conn, "C", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    linked = deck_dedupe.backfill(conn)
    assert linked == 2  # B and C, not A (it's the root)
    rows = {r["name"]: r["dup_of"] for r in conn.execute("SELECT name, dup_of FROM decks")}
    assert rows["A"] is None
    assert rows["B"] == a.deck_id
    assert rows["C"] == a.deck_id


def test_backfill_is_idempotent(conn):
    _store(conn, "A", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    _store(conn, "B", "Standard", "Deck\n4 Lightning Bolt (STA) 1\n4 Counterspell (STA) 2\n")
    deck_dedupe.backfill(conn)
    assert deck_dedupe.backfill(conn) == 0
