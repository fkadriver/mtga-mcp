"""Tests for importing a full collection from a MTGA-collection-exporter JSON export."""

from __future__ import annotations

import json

from mtga_mcp import db, ingest_export


def _export(cards: list[dict], export_date: str = "2026-08-11T17:47:14") -> dict:
    return {
        "export_date": export_date,
        "total_unique": len(cards),
        "total_cards": sum(c.get("count", 0) for c in cards),
        "cards": cards,
    }


def _write(tmp_path, payload: dict):
    p = tmp_path / "mtga_collection.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_single_printing_maps_count_directly(tmp_path):
    conn = db.connect(":memory:")
    path = _write(tmp_path, _export([
        {"count": 4, "name": "Final Showdown", "arena_ids": [90357]},
        {"count": 1, "name": "Land Tax", "arena_ids": [87047]},
    ]))
    res = ingest_export.ingest(conn, path)

    assert res.entries == 2
    assert res.grp_rows == 2
    assert res.total_copies == 5
    rows = dict(conn.execute("SELECT grp_id, count FROM collection").fetchall())
    assert rows == {90357: 4, 87047: 1}


def test_multi_printing_fills_each_to_playset_and_preserves_total(tmp_path):
    conn = db.connect(":memory:")
    path = _write(tmp_path, _export([
        {"count": 8, "name": "Ancestral Katana", "arena_ids": [79418, 85634]},  # 4 + 4
        {"count": 6, "name": "Some Rare", "arena_ids": [111, 222]},              # 4 + 2
        {"count": 36, "name": "Nazgul", "arena_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9]},  # 4 x 9
    ]))
    res = ingest_export.ingest(conn, path)

    rows = dict(conn.execute("SELECT grp_id, count FROM collection").fetchall())
    assert rows[79418] == 4 and rows[85634] == 4
    assert rows[111] == 4 and rows[222] == 2
    assert all(rows[i] == 4 for i in range(1, 10))
    # Totals are preserved regardless of the exact split.
    assert res.total_copies == 8 + 6 + 36


def test_import_replaces_existing_collection(tmp_path):
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO collection(grp_id, count) VALUES (999, 3)")
    conn.commit()

    path = _write(tmp_path, _export([{"count": 2, "name": "X", "arena_ids": [42]}]))
    ingest_export.ingest(conn, path)

    rows = dict(conn.execute("SELECT grp_id, count FROM collection").fetchall())
    assert rows == {42: 2}  # stale grp_id 999 is gone


def test_meta_records_source_and_export_date(tmp_path):
    conn = db.connect(":memory:")
    path = _write(tmp_path, _export([{"count": 1, "name": "X", "arena_ids": [42]}]))
    ingest_export.ingest(conn, path)

    assert db.get_meta(conn, "collection_source") == "memory-export:mtga_collection.json"
    assert db.get_meta(conn, "collection_export_date") == "2026-08-11T17:47:14"


def test_unknown_grp_ids_counted_against_catalog(tmp_path):
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO cards(grp_id, name) VALUES (42, 'Known Card')")
    conn.commit()
    path = _write(tmp_path, _export([
        {"count": 1, "name": "Known Card", "arena_ids": [42]},
        {"count": 1, "name": "Uncatalogued", "arena_ids": [7777]},
    ]))
    res = ingest_export.ingest(conn, path)

    assert res.grp_rows == 2
    assert res.unknown_grp_ids == 1  # 7777 isn't in `cards`


def test_bad_entries_skipped(tmp_path):
    conn = db.connect(":memory:")
    path = _write(tmp_path, _export([
        {"count": 0, "name": "Zero", "arena_ids": [1]},        # non-positive
        {"count": 2, "name": "NoIds", "arena_ids": []},        # no printings
        {"count": 3, "name": "Good", "arena_ids": [55]},
        {"name": "Malformed"},                                  # missing fields
    ]))
    res = ingest_export.ingest(conn, path)

    assert res.entries == 1
    rows = dict(conn.execute("SELECT grp_id, count FROM collection").fetchall())
    assert rows == {55: 3}


def test_non_export_json_raises(tmp_path):
    conn = db.connect(":memory:")
    p = tmp_path / "junk.json"
    p.write_text(json.dumps({"nope": True}), encoding="utf-8")
    try:
        ingest_export.ingest(conn, p)
    except ValueError as e:
        assert "cards" in str(e)
    else:
        raise AssertionError("expected ValueError for non-export JSON")
