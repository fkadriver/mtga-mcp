"""Tests for the inventory_raw schema migration (seq_id PK -> payload_hash-keyed)."""

from __future__ import annotations

import sqlite3

from mtga_mcp import db


def test_migrate_inventory_raw_preserves_old_rows_and_unblocks_seqid_reuse():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Simulate a pre-migration database: seq_id was the primary key.
    conn.executescript(
        """
        CREATE TABLE inventory_raw (
            seq_id      INTEGER PRIMARY KEY,
            captured_at TEXT NOT NULL,
            payload     TEXT NOT NULL
        );
        INSERT INTO inventory_raw VALUES
            (1, '2026-08-07T00:00:00Z', '{"SeqId":1,"Changes":[]}');
        """
    )

    db._migrate_inventory_raw(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(inventory_raw)")}
    assert "payload_hash" in cols
    old_row = conn.execute("SELECT * FROM inventory_raw WHERE seq_id = 1").fetchone()
    assert old_row["payload"] == '{"SeqId":1,"Changes":[]}'

    # A later session reusing seq_id=1 with different content must no longer collide.
    new_payload = '{"SeqId":1,"Changes":[{"GrantedCards":[{"GrpId":103489,"CardAdded":true}]}]}'
    conn.execute(
        "INSERT INTO inventory_raw(seq_id, payload_hash, captured_at, payload) VALUES(?,?,?,?)",
        (1, db.payload_hash(new_payload), "2026-08-11T12:00:00Z", new_payload),
    )
    assert conn.execute("SELECT COUNT(*) FROM inventory_raw").fetchone()[0] == 2


def test_migrate_inventory_raw_is_a_noop_on_fresh_schema():
    conn = db.connect(db_path=":memory:")
    before = {row[1] for row in conn.execute("PRAGMA table_info(inventory_raw)")}
    db._migrate_inventory_raw(conn)
    after = {row[1] for row in conn.execute("PRAGMA table_info(inventory_raw)")}
    assert before == after == {"id", "seq_id", "payload_hash", "captured_at", "payload"}
