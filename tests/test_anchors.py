"""Tests for anchor-card selection (DB-seeded candidates + the confirm/edit flow)."""

from __future__ import annotations

from mtga_mcp import anchors, db


def _conn():
    conn = db.connect(":memory:")
    conn.executemany(
        "INSERT INTO cards(grp_id, name, rarity) VALUES(?,?,?)",
        [
            (1, "Sheoldred, the Apocalypse", "mythic"),
            (2, "Lightning Bolt", "rare"),
            (3, "Island", "basic"),
            (4, "Counterspell", "uncommon"),
            (5, "Llanowar Elves", "rare"),
        ],
    )
    conn.executemany(
        "INSERT INTO collection(grp_id, count) VALUES(?,?)",
        [(1, 1), (2, 4), (4, 2), (5, 1)],
    )
    conn.commit()
    return conn


def _scripted(steps):
    it = iter(steps)
    return lambda _prompt: next(it)


def test_candidates_prefers_rares_mythics_then_count():
    c = anchors.candidates_from_db(_conn(), want=3)
    # mythic first, then rares by count desc: Sheoldred, Bolt(x4), Elves(x1). Uncommon excluded.
    assert [a.name for a in c] == ["Sheoldred, the Apocalypse", "Lightning Bolt", "Llanowar Elves"]
    assert c[0].quantity == 1 and c[1].quantity == 4


def test_resolve_card_case_insensitive():
    conn = _conn()
    a = anchors.resolve_card(conn, "lightning bolt")
    assert a is not None and a.arena_id == 2 and a.name == "Lightning Bolt" and a.quantity == 0
    assert anchors.resolve_card(conn, "Nonexistent") is None


def test_choose_anchors_accept_default():
    chosen = anchors.choose_anchors(_conn(), want=3, input_fn=_scripted([""]))
    assert len(chosen) == 3


def test_choose_anchors_edit_quantity():
    chosen = anchors.choose_anchors(
        _conn(), want=3, input_fn=_scripted(["e2", "3", ""])
    )
    # Row 2 (Lightning Bolt) quantity edited from 4 to 3.
    assert chosen[1].name == "Lightning Bolt" and chosen[1].quantity == 3


def test_choose_anchors_delete_below_min_then_readd():
    # Delete a row (down to 2), get told we need 3, add one back, then accept.
    chosen = anchors.choose_anchors(
        _conn(), want=3,
        input_fn=_scripted(["d1", "", "a", "Counterspell", "2", ""]),
    )
    assert len(chosen) == 3
    assert any(a.name == "Counterspell" and a.quantity == 2 for a in chosen)


def test_choose_anchors_manual_when_no_collection():
    conn = _conn()
    conn.execute("DELETE FROM collection")
    conn.commit()
    chosen = anchors.choose_anchors(
        conn, want=5,
        input_fn=_scripted([
            "a", "Lightning Bolt", "4",
            "a", "Sheoldred, the Apocalypse", "1",
            "a", "Llanowar Elves", "2",
            "",
        ]),
    )
    assert {a.name for a in chosen} == {
        "Lightning Bolt", "Sheoldred, the Apocalypse", "Llanowar Elves"
    }


def test_choose_anchors_quit_returns_empty():
    assert anchors.choose_anchors(_conn(), want=3, input_fn=_scripted(["q"])) == []
