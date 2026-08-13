"""Import a full owned-card collection from a memory-scanner export.

The modern MTGA client no longer logs the full owned-card list (see ``ingest_collection``),
so a `collection` built from Player.log only ever holds forward-looking ``GrantedCards``
deltas -- it can never recover cards owned before capture started. The memory scanner
(``memory_export``, run via ``mtga-mcp export-collection``) instead reads the full collection
out of the *running* client's memory. This module writes that snapshot into `collection`,
**replacing** it wholesale, via two entry points:

* ``ingest_block`` -- the built-in scanner hands us a raw ``{grp_id: quantity}`` mapping (exact
  per-printing counts), which we store directly. This is the preferred path.
* ``ingest`` -- loads a legacy MTGA-collection-exporter ``mtga_collection.json`` file, which
  aggregates owned copies by card *name* with every owned printing's ``arena_id`` (== our
  ``grp_id``) and the summed ``count``. Buildability sums counts back across a name's printings
  (``decklist.resolve_card``), so the exact per-printing split is immaterial; we distribute a
  name's count across its printings, filling each to the constructed playset cap (4) in listed
  order -- matching how MTGA caps owned copies per printing. (Verified against a real export:
  every count > 4 is a clean multiple of <=4 across its printings, e.g. Nazgul 36 == 9 x 4.)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db
from .decklist import PLAYSET


@dataclass
class ExportResult:
    entries: int           # card-name entries consumed from the export
    grp_rows: int          # per-grp_id rows written to `collection`
    total_copies: int      # sum of counts written
    unknown_grp_ids: int   # grp_ids written that aren't in our `cards` catalog
    export_date: str | None


def _distribute(count: int, arena_ids: list[int]) -> list[tuple[int, int]]:
    """Split a name's summed `count` across its printings, filling each to PLAYSET in order.

    The last printing absorbs any remainder (e.g. a basic owned beyond 4x per printing, or a
    data anomaly), so the per-name total is always preserved regardless of the split.
    """
    if not arena_ids:
        return []
    if len(arena_ids) == 1:
        return [(arena_ids[0], count)]
    rows: list[tuple[int, int]] = []
    remaining = count
    for aid in arena_ids[:-1]:
        take = min(PLAYSET, remaining)
        rows.append((aid, take))
        remaining -= take
    rows.append((arena_ids[-1], remaining))
    return rows


def _write_collection(
    conn: sqlite3.Connection,
    per_grp: dict[int, int],
    *,
    source: str,
    export_date: str | None,
) -> int:
    """Replace `collection` with per_grp and stamp provenance. Returns unknown-grp_id count."""
    with conn:
        conn.execute("DELETE FROM collection")
        conn.executemany(
            "INSERT INTO collection(grp_id, count) VALUES(?, ?)", per_grp.items()
        )
        db.set_meta(conn, "collection_source", source)
        if export_date:
            db.set_meta(conn, "collection_export_date", export_date)
    return conn.execute(
        "SELECT COUNT(*) FROM collection WHERE grp_id NOT IN (SELECT grp_id FROM cards)"
    ).fetchone()[0]


def ingest_block(
    conn: sqlite3.Connection,
    block: dict[int, int],
    *,
    source: str = "memory-scan",
    export_date: str | None = None,
) -> ExportResult:
    """Replace `collection` from a raw ``{grp_id: quantity}`` block (the built-in scanner path).

    These are exact per-printing counts straight from memory -- no name aggregation or
    distribute heuristic -- so we store them verbatim.
    """
    per_grp = {int(k): int(v) for k, v in block.items() if int(v) > 0}
    unknown = _write_collection(conn, per_grp, source=source, export_date=export_date)
    return ExportResult(
        entries=len(per_grp),
        grp_rows=len(per_grp),
        total_copies=sum(per_grp.values()),
        unknown_grp_ids=unknown,
        export_date=export_date,
    )


def ingest(conn: sqlite3.Connection, path: str | Path) -> ExportResult:
    """Replace `collection` with the owned cards in a MTGA-collection-exporter JSON export."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    cards = data.get("cards")
    if not isinstance(cards, list):
        raise ValueError(
            f"{path}: not a MTGA-collection-exporter export (no 'cards' array)"
        )

    # Fold into per-grp_id counts. A grp_id could in principle surface under two names
    # (e.g. an A-prefix alchemy variant); summing keeps that correct rather than clobbering.
    per_grp: dict[int, int] = {}
    entries = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        count = card.get("count")
        ids = card.get("arena_ids")
        if not isinstance(count, int) or count <= 0 or not isinstance(ids, list):
            continue
        ids = [i for i in ids if isinstance(i, int)]
        if not ids:
            continue
        entries += 1
        for grp, n in _distribute(count, ids):
            if n > 0:
                per_grp[grp] = per_grp.get(grp, 0) + n

    export_date = data.get("export_date")
    export_date = export_date if isinstance(export_date, str) else None

    unknown = _write_collection(
        conn, per_grp, source=f"memory-export:{path.name}", export_date=export_date
    )

    return ExportResult(
        entries=entries,
        grp_rows=len(per_grp),
        total_copies=sum(per_grp.values()),
        unknown_grp_ids=unknown,
        export_date=export_date,
    )
