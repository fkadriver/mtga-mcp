"""Tests for match result capture (win/loss + deck name parsed from Player.log)."""

from __future__ import annotations

import sqlite3

import pytest

from mtga_mcp import db, match_capture

AUTH = '{"authenticateResponse":{"screenName":"fkadriver"}}'


def _deck_select(event_name: str, deck_name: str, fmt: str = "Standard") -> str:
    # EventSetDeckV3 logs its own request body as a JSON *string* -- double-encoded, matching
    # the real shape (see match_capture.py's module docstring).
    inner = (
        '{\\"EventName\\":\\"%s\\",\\"Summary\\":{\\"DeckId\\":\\"d1\\",\\"Name\\":\\"%s\\",'
        '\\"Attributes\\":[{\\"name\\":\\"Format\\",\\"value\\":\\"%s\\"}]}}'
        % (event_name, deck_name, fmt)
    )
    return f'[UnityCrossThreadLogger]==> EventSetDeckV3 {{"id":"x","request":"{inner}"}}'


def _completed_match(match_id: str, *, my_team: int, winning_team: int, event_name="Play") -> str:
    return (
        '{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"gameRoomConfig":'
        '{"reservedPlayers":[{"playerName":"fkadriver","teamId":%d,"eventId":"%s"},'
        '{"playerName":"Opponent","teamId":%d,"eventId":"%s"}],"matchId":"%s"},'
        '"stateType":"MatchGameRoomStateType_MatchCompleted","finalMatchResult":'
        '{"matchId":"%s","resultList":['
        '{"scope":"MatchScope_Game","result":"ResultType_WinLoss","winningTeamId":%d},'
        '{"scope":"MatchScope_Match","result":"ResultType_WinLoss","winningTeamId":%d}]}}}}'
        % (my_team, event_name, 3 - my_team, event_name, match_id, match_id,
           winning_team, winning_team)
    )


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    return c


def _write_log(tmp_path, text: str):
    log = tmp_path / "Player.log"
    log.write_text(text)
    return log


def _fake_files(monkeypatch, *paths_):
    monkeypatch.setattr(match_capture.paths, "detailed_log_files", lambda max_utc=1: list(paths_))


def test_captures_a_win_with_its_deck(tmp_path, conn, monkeypatch):
    text = AUTH + "\n" + _deck_select("Play", "Black Demon") + "\n" + \
        _completed_match("m1", my_team=1, winning_team=1)
    log = _write_log(tmp_path, text)
    _fake_files(monkeypatch, log)
    res = match_capture.capture_matches(conn, force=True)
    assert res.new_matches == 1 and res.total_matches == 1
    row = conn.execute("SELECT * FROM match_history WHERE match_id='m1'").fetchone()
    assert row["won"] == 1
    assert row["deck_name"] == "Black Demon"
    assert row["format"] == "Standard"


def test_captures_a_loss(tmp_path, conn, monkeypatch):
    # Real sample: we were team 1, team 2 won -> a loss, exactly like the live-verified case.
    text = AUTH + "\n" + _deck_select("Play", "Black Demon") + "\n" + \
        _completed_match("m2", my_team=1, winning_team=2)
    log = _write_log(tmp_path, text)
    _fake_files(monkeypatch, log)
    res = match_capture.capture_matches(conn, force=True)
    assert res.new_matches == 1
    row = conn.execute("SELECT won FROM match_history WHERE match_id='m2'").fetchone()
    assert row["won"] == 0


def test_dedups_by_match_id(tmp_path, conn, monkeypatch):
    text = AUTH + "\n" + _deck_select("Play", "Black Demon") + "\n" + \
        _completed_match("m3", my_team=1, winning_team=1)
    log = _write_log(tmp_path, text)
    _fake_files(monkeypatch, log)
    match_capture.capture_matches(conn, force=True)
    res2 = match_capture.capture_matches(conn, force=True)
    assert res2.new_matches == 0
    assert res2.total_matches == 1


def test_picks_the_deck_select_matching_the_queue_event(tmp_path, conn, monkeypatch):
    # Two different queues selected different decks; the match was queued under "Play" --
    # must use that one's deck, not the unrelated "Traditional_Play" selection.
    text = (
        AUTH + "\n"
        + _deck_select("Traditional_Play", "Control Deck") + "\n"
        + _deck_select("Play", "Aggro Deck") + "\n"
        + _completed_match("m4", my_team=1, winning_team=1, event_name="Play")
    )
    log = _write_log(tmp_path, text)
    _fake_files(monkeypatch, log)
    match_capture.capture_matches(conn, force=True)
    row = conn.execute("SELECT deck_name FROM match_history WHERE match_id='m4'").fetchone()
    assert row["deck_name"] == "Aggro Deck"


def test_no_deck_select_still_records_the_result(tmp_path, conn, monkeypatch):
    text = AUTH + "\n" + _completed_match("m5", my_team=1, winning_team=1)
    log = _write_log(tmp_path, text)
    _fake_files(monkeypatch, log)
    res = match_capture.capture_matches(conn, force=True)
    assert res.new_matches == 1
    row = conn.execute("SELECT won, deck_name FROM match_history WHERE match_id='m5'").fetchone()
    assert row["won"] == 1
    assert row["deck_name"] is None


def test_skips_when_logs_unchanged(tmp_path, conn, monkeypatch):
    text = AUTH + "\n" + _completed_match("m6", my_team=1, winning_team=1)
    log = _write_log(tmp_path, text)
    _fake_files(monkeypatch, log)
    first = match_capture.capture_matches(conn)
    assert first.skipped is False and first.new_matches == 1
    second = match_capture.capture_matches(conn)
    assert second.skipped is True and second.new_matches == 0
