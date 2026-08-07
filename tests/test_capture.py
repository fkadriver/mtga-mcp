"""Tests for the inventory capture scaffold."""

from __future__ import annotations

import os
import sqlite3

import pytest

from mtga_mcp import capture, db

INV_1 = '{"InventoryInfo":{"SeqId":1,"Changes":[],"Gems":100,"Gold":5,"WildCardCommons":10}}'
INV_5 = (
    '{"InventoryInfo":{"SeqId":5,"Changes":[],"Gems":1380,"Gold":5,"TotalVaultProgress":25,'
    '"WildCardCommons":152,"WildCardUnCommons":121,"WildCardMythics":10}}'
)
INV_7_WITH_CHANGES = (
    '{"InventoryInfo":{"SeqId":7,"Changes":[{"source":"BoosterOpen"}],"Gems":1380,"Gold":5,'
    '"WildCardCommons":153}}'
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    return c


def _fake_logs(monkeypatch, text: str) -> None:
    """Bypass the filesystem: no log files, canned text (use force=True to skip mtime guard)."""
    monkeypatch.setattr(capture.paths, "detailed_log_files", lambda max_utc=1: [])
    monkeypatch.setattr(capture, "_read_all_logs", lambda files: text)


def test_capture_archives_each_seqid_and_snapshots_latest(conn, monkeypatch):
    _fake_logs(monkeypatch, INV_1 + "\n" + INV_5)
    res = capture.capture(conn, force=True)
    assert res.new_raw == 2
    assert res.total_raw == 2
    assert res.snapshot["common"] == 152  # highest SeqId (5) wins
    assert res.snapshot["mythic"] == 10
    assert res.snapshot["rare"] is None  # WildCardRares absent
    hist = conn.execute("SELECT common, mythic, gems FROM inventory_history").fetchone()
    assert (hist["common"], hist["mythic"], hist["gems"]) == (152, 10, 1380)


def test_capture_dedups_by_seqid(conn, monkeypatch):
    _fake_logs(monkeypatch, INV_1 + "\n" + INV_5)
    capture.capture(conn, force=True)
    res2 = capture.capture(conn, force=True)
    assert res2.new_raw == 0
    assert res2.total_raw == 2


def test_capture_accumulates_new_seqid_across_runs(conn, monkeypatch):
    _fake_logs(monkeypatch, INV_5)
    capture.capture(conn, force=True)
    monkeypatch.setattr(capture, "_read_all_logs", lambda files: INV_7_WITH_CHANGES)
    res = capture.capture(conn, force=True)
    assert res.new_raw == 1
    assert res.total_raw == 2
    assert res.changes_seen == 1  # populated Changes array is counted


def test_capture_handles_no_inventory(conn, monkeypatch):
    _fake_logs(monkeypatch, "nothing here")
    res = capture.capture(conn, force=True)
    assert res.new_raw == 0
    assert res.snapshot is None


def test_capture_skips_when_logs_unchanged(conn, tmp_path, monkeypatch):
    """The idle short-circuit: unchanged mtime -> no parsing, minimal CPU."""
    log = tmp_path / "Player.log"
    log.write_text(INV_5)
    monkeypatch.setattr(capture.paths, "detailed_log_files", lambda max_utc=1: [log])

    first = capture.capture(conn)
    assert first.skipped is False and first.new_raw == 1

    second = capture.capture(conn)  # mtime unchanged -> skipped
    assert second.skipped is True and second.new_raw == 0

    future = log.stat().st_mtime + 100
    os.utime(log, (future, future))
    third = capture.capture(conn)  # newer mtime -> parses again
    assert third.skipped is False
