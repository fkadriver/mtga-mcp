"""SQLite connection helpers for our own database."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from . import paths


def _schema_sql() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) our database and ensure the schema exists."""
    path = db_path or paths.DB_PATH
    paths.ensure_data_dir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_schema_sql())
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
