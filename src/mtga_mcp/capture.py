"""Capture MTGA inventory state over time.

Modern MTGA clients no longer log the full owned-card collection, only ``InventoryInfo``
payloads (wildcards + currency, plus a ``Changes`` delta array that is usually empty). And
Player.log rotates, so those payloads are ephemeral.

This module is the capture scaffold: run it regularly (see the LaunchAgent under
``packaging/``) to (1) accumulate every distinct ``InventoryInfo`` payload by ``SeqId`` into
``inventory_raw`` so future acquisition deltas are preserved across rotations, and (2) record
a flattened wildcard/currency snapshot in ``inventory_history`` for trend queries.

The card-delta parser — turning ``Changes`` entries into owned-card counts — is intentionally
deferred until a real pack-open / set release provides a populated ``Changes`` sample to build
against. See ``_apply_card_changes`` for the hook.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from . import db, paths
from .ingest_collection import _INVENTORY_FIELDS, _extract_all_json_objects

# meta key holding the newest log mtime we've already processed, to skip idle runs.
_MTIME_KEY = "capture_last_log_mtime"


@dataclass
class CaptureResult:
    new_raw: int          # distinct new InventoryInfo SeqIds archived this run
    total_raw: int        # total InventoryInfo payloads archived to date
    snapshot: dict | None  # the wildcard/currency snapshot recorded this run
    changes_seen: int     # non-empty Changes entries observed (for future card parsing)
    skipped: bool = False  # logs unchanged since last run -> parsing skipped


def _latest_log_mtime(files: list) -> float:
    return max((f.stat().st_mtime for f in files), default=0.0)


def _read_all_logs(files: list) -> str:
    parts: list[str] = []
    for path in files:
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def _snapshot_from_inventory(inv: dict) -> dict:
    snap = {kind: None for kind in ("common", "uncommon", "rare", "mythic", "gold", "gems", "vault")}
    for field, kind in _INVENTORY_FIELDS.items():
        value = inv.get(field)
        if isinstance(value, (int, float)):
            snap[kind] = int(value)
    return snap


def _apply_card_changes(conn: sqlite3.Connection, inv: dict) -> int:
    """Placeholder for the future card-delta parser.

    When ``InventoryInfo.Changes`` starts carrying card grants (e.g. after opening packs or a
    new set), this is where we will translate them into `collection` owned-count updates. We
    can't implement it correctly until a real populated sample exists, so for now we only count
    non-empty Changes so the operator can tell when there's data to build against.
    """
    changes = inv.get("Changes")
    return len(changes) if isinstance(changes, list) else 0


def capture(conn: sqlite3.Connection, *, force: bool = False) -> CaptureResult:
    # Cheap idle short-circuit: if no log file has changed since our last run, there is
    # nothing new to parse. This keeps the scheduled job near-free when you're not playing
    # (a few stat() calls instead of reading and parsing multi-MB logs).
    files = paths.detailed_log_files()
    latest_mtime = _latest_log_mtime(files)
    last_seen = db.get_meta(conn, _MTIME_KEY)
    total_raw = conn.execute("SELECT COUNT(*) FROM inventory_raw").fetchone()[0]
    if not force and last_seen is not None and latest_mtime <= float(last_seen):
        return CaptureResult(new_raw=0, total_raw=total_raw, snapshot=None,
                             changes_seen=0, skipped=True)

    text = _read_all_logs(files)
    now = datetime.now(timezone.utc).isoformat()

    inventories = _extract_all_json_objects(text, '"InventoryInfo"')
    new_raw = 0
    changes_seen = 0
    latest: tuple[int, dict] | None = None  # (seq_id, inventory) with the highest SeqId

    with conn:
        for inv in inventories:
            seq = inv.get("SeqId")
            if not isinstance(seq, int):
                continue
            changes_seen += _apply_card_changes(conn, inv)
            cur = conn.execute(
                "INSERT OR IGNORE INTO inventory_raw(seq_id, captured_at, payload) VALUES(?,?,?)",
                (seq, now, json.dumps(inv, separators=(",", ":"))),
            )
            new_raw += cur.rowcount
            if latest is None or seq > latest[0]:
                latest = (seq, inv)

        snapshot: dict | None = None
        if latest is not None:
            seq, inv = latest
            snapshot = _snapshot_from_inventory(inv)
            conn.execute(
                "INSERT INTO inventory_history(captured_at, seq_id, common, uncommon, rare, "
                "mythic, gold, gems, vault) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(captured_at) DO NOTHING",
                (now, seq, snapshot["common"], snapshot["uncommon"], snapshot["rare"],
                 snapshot["mythic"], snapshot["gold"], snapshot["gems"], snapshot["vault"]),
            )

        total_raw = conn.execute("SELECT COUNT(*) FROM inventory_raw").fetchone()[0]
        db.set_meta(conn, _MTIME_KEY, repr(latest_mtime))

    return CaptureResult(new_raw=new_raw, total_raw=total_raw,
                         snapshot=snapshot, changes_seen=changes_seen)
