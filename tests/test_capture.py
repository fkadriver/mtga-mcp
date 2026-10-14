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
# Shape observed in the wild: a "RedeemVoucher" Changes entry granting boosters plus one
# explicit card (see docs/latitude-setup.md capture history for the real sample).
INV_7_WITH_CHANGES = (
    '{"InventoryInfo":{"SeqId":7,"Changes":[{"Source":"RedeemVoucher",'
    '"GrantedCards":[{"GrpId":103489,"CardAdded":true,"SetCode":"HOB"}]}],'
    '"Gems":1380,"Gold":5,"WildCardCommons":153}}'
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._schema_sql())
    return c


def _fake_logs(monkeypatch, text: str) -> None:
    """Bypass the filesystem: no log files, canned text as a single "current" file
    (use force=True to skip mtime guard)."""
    monkeypatch.setattr(capture.paths, "detailed_log_files", lambda max_utc=1: [])
    monkeypatch.setattr(capture, "_read_logs", lambda files: [text])


def test_capture_archives_each_seqid_and_snapshots_latest(conn, monkeypatch):
    _fake_logs(monkeypatch, INV_1 + "\n" + INV_5)
    res = capture.capture(conn, force=True)
    assert res.new_raw == 2
    assert res.total_raw == 2
    assert res.snapshot["common"] == 152  # highest SeqId (5) wins
    assert res.snapshot["mythic"] == 10
    assert res.snapshot["rare"] == 0  # WildCardRares absent -> MTGA omits it at 0, not unknown
    hist = conn.execute("SELECT common, mythic, gems FROM inventory_history").fetchone()
    assert (hist["common"], hist["mythic"], hist["gems"]) == (152, 10, 1380)


def test_capture_syncs_wildcards_table_from_snapshot(conn, monkeypatch):
    """`deck_analysis`'s craftable/buildable checks and collection_summary read the
    `wildcards` table, not `inventory_history` -- it must track the live snapshot too, or it
    goes stale forever after the one-time export-collection/import-collection scan that
    originally populated it."""
    conn.executemany(
        "INSERT INTO wildcards(kind, count) VALUES(?,?)",
        [("rare", 6), ("mythic", 13)],  # stale values from a prior export-collection run
    )
    conn.commit()
    _fake_logs(monkeypatch, INV_1)  # no WildCardRares key -> current rare count is 0
    capture.capture(conn, force=True)
    wc = {r["kind"]: r["count"] for r in conn.execute("SELECT kind, count FROM wildcards")}
    assert wc["rare"] == 0  # overwritten, not left at the stale 6
    assert wc["common"] == 10  # INV_1's WildCardCommons


def test_capture_dedups_by_seqid(conn, monkeypatch):
    _fake_logs(monkeypatch, INV_1 + "\n" + INV_5)
    capture.capture(conn, force=True)
    res2 = capture.capture(conn, force=True)
    assert res2.new_raw == 0
    assert res2.total_raw == 2


def test_capture_accumulates_new_seqid_across_runs(conn, monkeypatch):
    _fake_logs(monkeypatch, INV_5)
    capture.capture(conn, force=True)
    monkeypatch.setattr(capture, "_read_logs", lambda files: [INV_7_WITH_CHANGES])
    res = capture.capture(conn, force=True)
    assert res.new_raw == 1
    assert res.total_raw == 2
    assert res.changes_seen == 1  # one GrantedCards copy applied


def test_capture_applies_granted_cards_to_collection(conn, monkeypatch):
    _fake_logs(monkeypatch, INV_7_WITH_CHANGES)
    capture.capture(conn, force=True)
    row = conn.execute("SELECT count FROM collection WHERE grp_id = 103489").fetchone()
    assert row["count"] == 1


def test_capture_does_not_double_apply_on_rerun(conn, monkeypatch):
    """The same log text is reread on every run (rotation-tolerant); GrantedCards must only
    be applied once per distinct payload, not once per capture() call."""
    _fake_logs(monkeypatch, INV_7_WITH_CHANGES)
    capture.capture(conn, force=True)
    res2 = capture.capture(conn, force=True)
    assert res2.new_raw == 0
    assert res2.changes_seen == 0
    row = conn.execute("SELECT count FROM collection WHERE grp_id = 103489").fetchone()
    assert row["count"] == 1


def test_capture_archives_both_sides_of_a_seqid_collision(conn, monkeypatch):
    """SeqId resets every MTGA session, so two different real payloads can share a SeqId --
    both must be archived and both sets of GrantedCards applied, not silently dropped."""
    same_seq_other_session = (
        '{"InventoryInfo":{"SeqId":7,"Changes":[{"Source":"BoosterOpen",'
        '"GrantedCards":[{"GrpId":42,"CardAdded":true}]}],"Gems":1380,"Gold":5,'
        '"WildCardCommons":153}}'
    )
    _fake_logs(monkeypatch, INV_7_WITH_CHANGES)
    capture.capture(conn, force=True)
    monkeypatch.setattr(capture, "_read_logs", lambda files: [same_seq_other_session])
    res = capture.capture(conn, force=True)
    assert res.new_raw == 1
    assert res.total_raw == 2
    assert conn.execute("SELECT count FROM collection WHERE grp_id = 42").fetchone()["count"] == 1
    assert conn.execute(
        "SELECT count FROM collection WHERE grp_id = 103489"
    ).fetchone()["count"] == 1


def test_capture_snapshot_prefers_current_file_over_higher_stale_seqid(conn, monkeypatch):
    """A restart rotates Player.log and resets SeqId to 1; the previous session's
    Player-prev.log can still be sitting there with a numerically higher SeqId. The snapshot
    must reflect the current file (SeqId 1, no WildCardRares key -> rare absent/0), not the
    stale prev-log entry (SeqId 5, rare wildcards present) just because 5 > 1."""
    monkeypatch.setattr(capture.paths, "detailed_log_files", lambda max_utc=1: [])
    current_session = INV_1  # SeqId 1, fresh post-restart Player.log
    stale_prev_session = INV_5  # SeqId 5, left over in Player-prev.log from before the restart
    monkeypatch.setattr(capture, "_read_logs", lambda files: [current_session, stale_prev_session])
    res = capture.capture(conn, force=True)
    assert res.snapshot["common"] == 10  # INV_1's value, not INV_5's 152
    assert res.snapshot["rare"] == 0  # INV_1 has no WildCardRares key -> 0, not INV_5's rares
    assert res.new_raw == 2  # both still archived into inventory_raw


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
