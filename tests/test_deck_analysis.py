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
        "INSERT INTO cards(grp_id,name,set_code,collector_number,rarity,type_line,legal_standard)"
        " VALUES(?,?,?,?,?,?,?)",
        [
            (1, "Lightning Bolt", "STA", "42", "rare", "Instant", "legal"),
            (2, "Sheoldred, the Apocalypse", "DMU", "107", "mythic", "Legendary Creature", "legal"),
            (3, "Counterspell", "STA", "16", "uncommon", "Instant", "legal"),
            (4, "Island", "FDN", "270", "basic", "Basic Land — Island", "legal"),
            (5, "Fury of the Horde", "ME1", "1", "rare", "Instant", "not_legal"),  # rotated
        ],
    )
    # Own a full playset of Bolt and 2 Sheoldred; no Counterspell. Own the rotated card too.
    c.executemany("INSERT INTO collection(grp_id,count) VALUES(?,?)", [(1, 4), (2, 2), (5, 4)])
    c.executemany(
        "INSERT INTO wildcards(kind,count) VALUES(?,?)",
        [("common", 5), ("uncommon", 10), ("rare", 3), ("mythic", 3)],
    )
    # Deck A: fully buildable Bo1, decent win rate.
    _store(c, "Aggro", "Standard", best_of=1, win_rate=0.55,
           text="Deck\n4 Lightning Bolt (STA) 42\n2 Sheoldred, the Apocalypse (DMU) 107\n4 Island (FDN) 270\n")
    # Deck B: strong win rate but missing 4 Counterspell.
    _store(c, "Control", "Standard", best_of=3, win_rate=0.65,
           text="Deck\n4 Counterspell (STA) 16\n2 Sheoldred, the Apocalypse (DMU) 107\n")
    # Deck C: fringe Bo1, no win rate reported, missing 3 Counterspell.
    _store(c, "Tempo", "Standard", best_of=1,
           text="Deck\n4 Lightning Bolt (STA) 42\n3 Counterspell (STA) 16\n")
    # Deck D: fully owned + high win rate, but runs a rotated card -> illegal for Standard.
    _store(c, "Rogue", "Standard", best_of=1, meta_share=0.9, win_rate=0.9,
           text="Deck\n4 Fury of the Horde (ME1) 1\n4 Lightning Bolt (STA) 42\n")
    return c


def _store(c, name, fmt, *, best_of=None, tier=None, meta_share=None, win_rate=None, text=""):
    decklist.store_deck(
        c, name=name, fmt=fmt, cards=decklist.parse_decklist(text),
        source="text", best_of=best_of, tier=tier, meta_share=meta_share, win_rate=win_rate,
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


def test_craftable_now_flag(conn):
    res = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn)}
    # Aggro is fully owned: buildable, not "craftable" (nothing to spend).
    assert res["Aggro"]["buildable_now"] is True and res["Aggro"]["craftable_now"] is False
    # Control needs 4 uncommon Counterspell; fixture owns 10 uncommon WCs -> flips w/ wildcards.
    assert res["Control"]["buildable_now"] is False
    assert res["Control"]["craftable_now"] is True
    # Drop uncommon wildcards below the need -> no longer craftable.
    conn.execute("UPDATE wildcards SET count = 2 WHERE kind = 'uncommon'")
    res2 = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn)}
    assert res2["Control"]["craftable_now"] is False


def test_arena_decklist_roundtrips_with_sideboard(conn):
    _store(conn, "WithSB", "Standard", best_of=3,
           text="Deck\n4 Lightning Bolt (STA) 42\n4 Island (FDN) 270\n\n"
                "Sideboard\n2 Counterspell (STA) 16\n")
    out = deck_analysis.arena_decklist(conn, "WithSB")
    assert out.startswith("Deck\n")
    assert "4 Lightning Bolt" in out and "4 Island" in out
    assert "\nSideboard\n2 Counterspell" in out
    # Parsing our own export back yields the same cards -> importable form is faithful.
    reparsed = {(c.name, c.board): c.quantity for c in decklist.parse_decklist(out)}
    assert reparsed[("Lightning Bolt", "main")] == 4
    assert reparsed[("Counterspell", "side")] == 2


def test_gap_excludes_basics(conn):
    gap = deck_analysis.deck_gap(conn, "Aggro")
    assert all(m["name"] != "Island" for m in gap["missing_cards"])


def test_gap_caps_at_playset(conn):
    # Tempo runs 3 Counterspell -> needs exactly 3, not more.
    gap = deck_analysis.deck_gap(conn, "Tempo")
    cs = next(m for m in gap["missing_cards"] if m["name"] == "Counterspell")
    assert cs["missing"] == 3


def test_best_buildable_ranks_by_buildability_plus_win_rate(conn):
    ranked = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn)}
    # Aggro: buildability 1.0 + win_rate 0.55 = 1.55
    # Control: buildability 0.2 (needs 4 uncommon) + win_rate 0.65 = 0.85
    # Tempo: buildability 0.25 (needs 3 uncommon) + win_rate 0 (unreported) = 0.25
    assert ranked["Aggro"]["score"] == pytest.approx(1.55)
    assert ranked["Control"]["score"] == pytest.approx(0.85)
    assert ranked["Tempo"]["score"] == pytest.approx(0.25)
    order = [r["name"] for r in deck_analysis.best_buildable_deck(conn)]
    assert order == ["Aggro", "Control", "Tempo"]
    assert ranked["Aggro"]["buildable_now"] is True


def test_best_buildable_unreported_win_rate_is_not_penalized(conn):
    # A deck with no win_rate at all scores exactly its buildability (a 0 bonus, not a
    # multiplier that would otherwise zero it out or rank it below a reported-but-poor deck).
    _store(conn, "Mystery", "Pioneer", best_of=1, text="Deck\n4 Island (FDN) 270\n")
    _store(conn, "KnownWeak", "Pioneer", best_of=1, win_rate=0.1,
           text="Deck\n4 Island (FDN) 270\n")
    ranked = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn, fmt="Pioneer")}
    assert ranked["Mystery"]["score"] == pytest.approx(1.0)  # buildability 1.0 + 0
    assert ranked["KnownWeak"]["score"] == pytest.approx(1.1)  # buildability 1.0 + 0.1
    assert ranked["Mystery"]["win_rate"] is None


def test_best_buildable_averages_multiple_known_signals(conn):
    # A deck with win_rate 0.4 AND tier "1" (weight 1.0) averages to 0.7 -- it does not sum
    # to 1.4 just because it happens to carry two known signals instead of one.
    _store(conn, "MultiSignal", "Explorer", best_of=1, win_rate=0.4, tier="1",
           text="Deck\n4 Island (FDN) 270\n")
    _store(conn, "SingleSignal", "Explorer", best_of=1, win_rate=0.7,
           text="Deck\n4 Island (FDN) 270\n")
    ranked = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn, fmt="Explorer")}
    assert ranked["MultiSignal"]["score"] == pytest.approx(1.7)   # 1.0 + avg(0.4, 1.0)
    assert ranked["SingleSignal"]["score"] == pytest.approx(1.7)  # 1.0 + 0.7, same magnitude


def test_best_buildable_folds_in_local_win_rate_by_deck_name(conn):
    # No win_rate/meta_share/tier on this deck -- only a tracked local win rate, joined by
    # name (case-insensitive) against match_history.
    _store(conn, "Black Demon", "Explorer", best_of=1, text="Deck\n4 Island (FDN) 270\n")
    conn.executemany(
        "INSERT INTO match_history(match_id, deck_name, format, won, played_at) "
        "VALUES (?,?,?,?,?)",
        [
            ("m1", "black demon", "Standard", 1, "2026-10-15T00:00:00+00:00"),
            ("m2", "black demon", "Standard", 0, "2026-10-15T00:01:00+00:00"),
            ("m3", "black demon", "Standard", 1, "2026-10-15T00:02:00+00:00"),
        ],
    )
    ranked = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn, fmt="Explorer")}
    assert ranked["Black Demon"]["local_win_rate"] == pytest.approx(2 / 3)
    assert ranked["Black Demon"]["score"] == round(1.0 + 2 / 3, 4)  # buildability 1.0 + bonus


def test_best_buildable_normalizes_meta_share_within_the_average(conn):
    _store(conn, "TopMeta", "Timeless", best_of=1, meta_share=0.10, text="Deck\n4 Island (FDN) 270\n")
    _store(conn, "FringeMeta", "Timeless", best_of=1, meta_share=0.02, text="Deck\n4 Island (FDN) 270\n")
    ranked = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn, fmt="Timeless")}
    assert ranked["TopMeta"]["score"] == pytest.approx(2.0)    # 1.0 + (0.10 / 0.10)
    assert ranked["FringeMeta"]["score"] == pytest.approx(1.2)  # 1.0 + (0.02 / 0.10)


def test_best_buildable_includes_source(conn):
    ranked = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn)}
    assert ranked["Aggro"]["source"] == "text"


def test_best_buildable_filters_best_of(conn):
    bo3 = [r["name"] for r in deck_analysis.best_buildable_deck(conn, best_of=3)]
    assert bo3 == ["Control"]
    bo1 = {r["name"] for r in deck_analysis.best_buildable_deck(conn, best_of=1)}
    assert bo1 == {"Aggro", "Tempo"}


def test_best_buildable_max_wildcards(conn):
    only_free = deck_analysis.best_buildable_deck(conn, max_wildcards=0)
    assert [r["name"] for r in only_free] == ["Aggro"]


def test_best_buildable_excludes_illegal_by_default(conn):
    ranked = deck_analysis.best_buildable_deck(conn)
    names = [r["name"] for r in ranked]
    # Rogue has the highest meta share (0.9) and is fully owned, but runs a rotated card,
    # so legality enforcement drops it entirely.
    assert "Rogue" not in names
    assert names == ["Aggro", "Control", "Tempo"]
    assert all(r["format_legal"] is True for r in ranked)


def test_best_buildable_include_illegal_flags_but_keeps(conn):
    ranked = deck_analysis.best_buildable_deck(conn, include_illegal=True)
    by_name = {r["name"]: r for r in ranked}
    assert "Rogue" in by_name
    rogue = by_name["Rogue"]
    assert rogue["format_legal"] is False
    assert [c["name"] for c in rogue["illegal_cards"]] == ["Fury of the Horde"]
    # It still ranks (top, on buildability + win_rate) when illegal decks are included.
    assert ranked[0]["name"] == "Rogue"
    assert by_name["Aggro"]["illegal_cards"] == []


def test_unknown_format_not_legality_excluded(conn):
    _store(conn, "Brew", "Alchemy", best_of=1, meta_share=0.5,
           text="Deck\n4 Fury of the Horde (ME1) 1\n")  # rotated in Standard, but format=Alchemy
    ranked = {r["name"]: r for r in deck_analysis.best_buildable_deck(conn)}
    assert "Brew" in ranked                       # Alchemy has no legality data -> not excluded
    assert ranked["Brew"]["format_legal"] is None


def test_craft_priority_unlocks_most_decks(conn):
    ranked = deck_analysis.craft_priority(conn)
    top = ranked[0]
    assert top["name"] == "Counterspell"
    assert top["rarity"] == "uncommon"
    assert top["decks_needing"] == 2
    assert top["decks_completed_if_crafted"] == 2
    assert top["copies_needed"] == 7  # 4 (Control) + 3 (Tempo)
