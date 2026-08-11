"""Capture MTGA inventory state over time.

Modern MTGA clients no longer log the full owned-card collection, only ``InventoryInfo``
payloads: wildcards + currency, plus a ``Changes`` delta array. ``Changes`` is empty on most
payloads (periodic snapshots) but is populated on real acquisition events -- pack opens,
precon grants, bundle/voucher redemptions -- as a list of entries that may carry a
``GrantedCards`` list (one dict per physical copy granted). And Player.log rotates, so these
payloads are ephemeral unless archived.

This module runs regularly (see the LaunchAgent/systemd timer under ``packaging/``) to
(1) accumulate every distinct ``InventoryInfo`` payload into ``inventory_raw`` so acquisition
deltas are preserved across rotations, (2) record a flattened wildcard/currency snapshot in
``inventory_history`` for trend queries, and (3) apply any ``GrantedCards`` entries to the
`collection` table (see ``_apply_card_changes``).

Note ``collection`` counts built this way only reflect deltas captured *since this parser
started running* -- there is no full historical collection to reconcile against, since modern
clients don't log one. A card owned before capture ran, that hasn't been granted again since,
won't show up here.
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
    changes_seen: int     # GrantedCards copies applied to `collection` this run
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
    """Translate ``InventoryInfo.Changes[].GrantedCards`` into `collection` count updates.

    Each ``Changes`` entry is one acquisition event (pack open, precon grant, voucher
    redemption, ...); its optional ``GrantedCards`` list has one dict per physical copy, e.g.
    ``{"GrpId": 103489, "CardAdded": true, "SetCode": "HOB"}``. We've only observed
    ``CardAdded: true`` in the wild but honor an explicit ``false`` as a removal, since that's
    the only plausible meaning of the field. Returns the number of copies applied, for
    operator visibility (see `mtga-mcp capture` output).

    Caller must only invoke this for payloads it hasn't already archived (see `capture()`) --
    it mutates `collection` unconditionally, so applying the same payload twice would
    double-count.
    """
    changes = inv.get("Changes")
    if not isinstance(changes, list):
        return 0
    applied = 0
    for change in changes:
        granted = change.get("GrantedCards") if isinstance(change, dict) else None
        if not isinstance(granted, list):
            continue
        for card in granted:
            if not isinstance(card, dict):
                continue
            grp_id = card.get("GrpId")
            if not isinstance(grp_id, int):
                continue
            delta = 1 if card.get("CardAdded", True) else -1
            conn.execute(
                "INSERT INTO collection(grp_id, count) VALUES(?, ?) "
                "ON CONFLICT(grp_id) DO UPDATE SET count = MAX(count + excluded.count, 0)",
                (grp_id, delta),
            )
            applied += 1
    return applied


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
            payload = json.dumps(inv, separators=(",", ":"))
            cur = conn.execute(
                "INSERT OR IGNORE INTO inventory_raw(seq_id, payload_hash, captured_at, payload) "
                "VALUES(?,?,?,?)",
                (seq, db.payload_hash(payload), now, payload),
            )
            new_raw += cur.rowcount
            if cur.rowcount:
                # Only apply deltas the first time we archive a payload -- the log file
                # commonly still contains this same entry on the next run, and re-applying it
                # would double-count the cards it granted.
                changes_seen += _apply_card_changes(conn, inv)
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
