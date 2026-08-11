"""SQLite connection helpers for our own database."""

from __future__ import annotations

import hashlib
import sqlite3
from importlib import resources
from pathlib import Path

from . import paths


def _schema_sql() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


def payload_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _migrate_inventory_raw(conn: sqlite3.Connection) -> None:
    """Upgrade a pre-existing `inventory_raw` (seq_id INTEGER PRIMARY KEY) to the
    payload_hash-keyed schema, preserving already-archived rows. SeqId resets every MTGA
    session, so the old PK silently dropped any new payload that collided with a seq_id
    already seen in an earlier session -- this replays old rows into the new table rather
    than losing them.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(inventory_raw)")}
    if not cols or "payload_hash" in cols:
        return
    with conn:
        conn.execute("ALTER TABLE inventory_raw RENAME TO inventory_raw_old")
        conn.executescript(_schema_sql())
        for seq_id, captured_at, payload in conn.execute(
            "SELECT seq_id, captured_at, payload FROM inventory_raw_old"
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO inventory_raw(seq_id, payload_hash, captured_at, payload) "
                "VALUES (?, ?, ?, ?)",
                (seq_id, payload_hash(payload), captured_at, payload),
            )
        conn.execute("DROP TABLE inventory_raw_old")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) our database and ensure the schema exists."""
    path = db_path or paths.DB_PATH
    paths.ensure_data_dir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_schema_sql())
    _migrate_inventory_raw(conn)
    return conn


def connect_readonly(db_path: Path | None = None) -> sqlite3.Connection:
    """Open our database read-only (used for ad-hoc user/LLM SQL)."""
    path = db_path or paths.DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(f"Database {path} does not exist yet; run an import first.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
