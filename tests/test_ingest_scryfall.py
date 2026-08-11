"""Tests for Scryfall enrichment, focusing on the name+set fallback pass."""

from __future__ import annotations

from mtga_mcp import db, ingest_scryfall as sf


def _seed(conn, rows):
    """rows: list of (grp_id, name, set_code)."""
    conn.executemany(
        "INSERT INTO cards(grp_id, name, set_code) VALUES (?,?,?)", rows
    )
    conn.commit()


def _card(name, *, arena_id=None, set_="tst", type_line="Creature", legal="legal",
          scryfall_id="sid", usd="1.5"):
    c = {
        "id": scryfall_id, "name": name, "set": set_, "type_line": type_line,
        "legalities": {"standard": legal}, "prices": {"usd": usd},
        "color_identity": ["G"], "keywords": [],
    }
    if arena_id is not None:
        c["arena_id"] = arena_id
    return c


def test_arena_pass_matches_by_grp_id():
    conn = db.connect(":memory:")
    _seed(conn, [(100, "Llanowar Elves", "dmu")])
    n = sf._apply_arena_matches(conn, [_card("Llanowar Elves", arena_id=100, type_line="Elf")])
    assert n == 1
    row = conn.execute("SELECT type_line, scryfall_id FROM cards WHERE grp_id=100").fetchone()
    assert row["type_line"] == "Elf" and row["scryfall_id"] == "sid"


def test_name_set_fallback_enriches_arena_id_miss():
    conn = db.connect(":memory:")
    # MTGA printing with no matching arena_id in the bulk data.
    _seed(conn, [(200, "Wasteland", "TMP")])
    # A Scryfall card of the same name+set but *without* arena_id -> arena pass skips it.
    cards = [_card("Wasteland", set_="tmp", type_line="Land", scryfall_id="w1")]

    assert sf._apply_arena_matches(conn, cards) == 0     # no arena_id -> no match
    assert sf._apply_name_fallback(conn, cards) == 1
    row = conn.execute("SELECT type_line, scryfall_id FROM cards WHERE grp_id=200").fetchone()
    assert row["type_line"] == "Land" and row["scryfall_id"] == "w1"


def test_exact_set_match_wins_over_name_only():
    conn = db.connect(":memory:")
    _seed(conn, [(300, "Mox Diamond", "STH")])
    cards = [
        _card("Mox Diamond", set_="sth", usd="90", scryfall_id="sth-print"),   # exact set
        _card("Mox Diamond", set_="2xm", usd="80", scryfall_id="2xm-print"),   # other printing
    ]
    sf._apply_name_fallback(conn, cards)
    row = conn.execute("SELECT scryfall_id, prices_usd FROM cards WHERE grp_id=300").fetchone()
    assert row["scryfall_id"] == "sth-print" and row["prices_usd"] == 90.0


def test_name_only_used_when_no_set_match():
    conn = db.connect(":memory:")
    _seed(conn, [(400, "Necromancy", "VIS")])  # MTGA set code Scryfall won't have
    cards = [_card("Necromancy", set_="6ed", type_line="Enchantment", scryfall_id="n1")]
    assert sf._apply_name_fallback(conn, cards) == 1
    assert conn.execute("SELECT scryfall_id FROM cards WHERE grp_id=400").fetchone()[0] == "n1"


def test_fallback_skips_already_enriched_and_unrelated():
    conn = db.connect(":memory:")
    _seed(conn, [(500, "Already Done", "dmu"), (600, "No Source", "dmu")])
    conn.execute("UPDATE cards SET scryfall_id='pre' WHERE grp_id=500")  # already enriched
    conn.commit()
    cards = [_card("Already Done", set_="dmu", scryfall_id="new")]
    sf._apply_name_fallback(conn, cards)
    # 500 keeps its existing enrichment (excluded from the pending set); 600 has no source.
    assert conn.execute("SELECT scryfall_id FROM cards WHERE grp_id=500").fetchone()[0] == "pre"
    assert conn.execute("SELECT scryfall_id FROM cards WHERE grp_id=600").fetchone()[0] is None


def test_alchemy_prefix_not_name_matched():
    conn = db.connect(":memory:")
    _seed(conn, [(700, "A-The Meathook Massacre", "vow")])
    cards = [_card("The Meathook Massacre", set_="vow", scryfall_id="base")]
    assert sf._apply_name_fallback(conn, cards) == 0  # A- name != base name, no false match
    assert conn.execute("SELECT scryfall_id FROM cards WHERE grp_id=700").fetchone()[0] is None
