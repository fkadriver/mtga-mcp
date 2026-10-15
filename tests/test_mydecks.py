"""Tests for importing your own Arena decks from Player.log's DeckSummaries/DecksInternal."""

from __future__ import annotations

import json
import sqlite3

import pytest

from mtga_mcp import db, mydecks


def _dump(summaries: list[dict], internals: dict) -> str:
    # Mirrors the real log shape: both keys sit inside some larger enclosing object.
    return (
        '{"DeckSummariesCacheVersion":1,"DeckSummaries":' + json.dumps(summaries) + "}\n"
        '{"DecksCacheVersion":1,"DecksInternal":' + json.dumps(internals) + "}\n"
    )


def _summary(deck_id: str, name: str, fmt: str = "Standard") -> dict:
    return {"DeckIdInternal": deck_id, "Name": name,
            "Attributes": [{"name": "Format", "value": fmt}]}


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    c.executemany(
        "INSERT INTO cards(grp_id, name, set_code, rarity) VALUES (?,?,?,?)",
        [(100, "Lightning Bolt", "STA", "rare"), (200, "Island", "FDN", "basic")],
    )
    c.commit()
    return c


def _fake_log(tmp_path, monkeypatch, text: str):
    log = tmp_path / "Player.log"
    log.write_text(text)
    monkeypatch.setattr(mydecks.paths, "detailed_log_files", lambda max_utc=1: [log])


def test_imports_main_and_sideboard(tmp_path, conn, monkeypatch):
    summaries = [_summary("d1", "Bolt Deck", "Standard")]
    internals = {"d1": {
        "MainDeck": [{"cardId": 100, "quantity": 4}, {"cardId": 200, "quantity": 20}],
        "Sideboard": [{"cardId": 100, "quantity": 2}],
    }}
    _fake_log(tmp_path, monkeypatch, _dump(summaries, internals))
    res = mydecks.import_my_decks(conn)
    assert res.imported == 1 and res.skipped == 0
    row = conn.execute("SELECT id, format FROM decks WHERE name='Bolt Deck'").fetchone()
    assert row["format"] == "Standard"
    cards = conn.execute(
        "SELECT card_name, quantity, board FROM deck_cards WHERE deck_id=?", (row["id"],)
    ).fetchall()
    by_board = {(c["card_name"], c["board"]): c["quantity"] for c in cards}
    assert by_board[("Lightning Bolt", "main")] == 4
    assert by_board[("Island", "main")] == 20
    assert by_board[("Lightning Bolt", "side")] == 2


def test_skips_already_imported(tmp_path, conn, monkeypatch):
    summaries = [_summary("d1", "Bolt Deck")]
    internals = {"d1": {"MainDeck": [{"cardId": 100, "quantity": 4}]}}
    _fake_log(tmp_path, monkeypatch, _dump(summaries, internals))
    mydecks.import_my_decks(conn)
    res2 = mydecks.import_my_decks(conn)
    assert res2.imported == 0 and res2.skipped == 1


def test_name_filter(tmp_path, conn, monkeypatch):
    summaries = [_summary("d1", "Bolt Deck"), _summary("d2", "Control Deck")]
    internals = {
        "d1": {"MainDeck": [{"cardId": 100, "quantity": 4}]},
        "d2": {"MainDeck": [{"cardId": 200, "quantity": 4}]},
    }
    _fake_log(tmp_path, monkeypatch, _dump(summaries, internals))
    res = mydecks.import_my_decks(conn, name_filter="bolt")
    assert res.imported == 1
    assert conn.execute("SELECT COUNT(*) c FROM decks").fetchone()["c"] == 1


def test_unknown_card_id_reported_but_deck_still_stored(tmp_path, conn, monkeypatch):
    summaries = [_summary("d1", "Mixed Deck")]
    internals = {"d1": {"MainDeck": [
        {"cardId": 100, "quantity": 4}, {"cardId": 999999, "quantity": 1},
    ]}}
    _fake_log(tmp_path, monkeypatch, _dump(summaries, internals))
    res = mydecks.import_my_decks(conn)
    assert res.imported == 1
    assert res.unresolved_cards == {"Mixed Deck": [999999]}


def test_deck_with_no_resolvable_cards_is_skipped(tmp_path, conn, monkeypatch):
    summaries = [_summary("d1", "All Unknown")]
    internals = {"d1": {"MainDeck": [{"cardId": 999999, "quantity": 4}]}}
    _fake_log(tmp_path, monkeypatch, _dump(summaries, internals))
    res = mydecks.import_my_decks(conn)
    assert res.imported == 0
    assert conn.execute("SELECT COUNT(*) c FROM decks").fetchone()["c"] == 0


def test_uses_the_latest_occurrence_when_logged_more_than_once(tmp_path, conn, monkeypatch):
    # The real log re-logs the same DeckSummaries/DecksInternal dump repeatedly as a session
    # goes on; a deck renamed/rebuilt between occurrences should resolve to the newest state.
    stale = _dump([_summary("d1", "Old Name")], {"d1": {"MainDeck": [{"cardId": 200, "quantity": 4}]}})
    fresh = _dump([_summary("d1", "New Name")], {"d1": {"MainDeck": [{"cardId": 100, "quantity": 4}]}})
    _fake_log(tmp_path, monkeypatch, stale + fresh)
    res = mydecks.import_my_decks(conn)
    assert res.imported == 1
    names = [r["name"] for r in conn.execute("SELECT name FROM decks")]
    assert names == ["New Name"]
