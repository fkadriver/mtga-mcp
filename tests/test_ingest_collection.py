"""Tests for parsing MTGA log inventory payloads."""

from __future__ import annotations

from mtga_mcp import db, ingest_collection as ic

# A trimmed real InventoryInfo line (modern schema) with an earlier, stale one before it.
MODERN_LOG = (
    'stuff {"InventoryInfo":{"SeqId":1,"Gems":100,"Gold":5,"WildCardCommons":1}} more\n'
    '{"InventoryInfo":{"SeqId":9,"Changes":[],"Gems":1380,"Gold":5,'
    '"TotalVaultProgress":25,"WildCardCommons":152,"WildCardUnCommons":121,'
    '"WildCardMythics":10,"CustomTokens":{"DraftToken":2}}}\n'
)

LEGACY_LOG = (
    '<== PlayerInventory.GetPlayerInventory {"wcCommon":7,"wcUncommon":3,'
    '"wcRare":2,"wcMythic":1,"gold":500,"gems":200,"vaultProgress":40}\n'
)


def _map_fields(inv, fields):
    return {kind: int(inv[f]) for f, kind in fields.items() if f in inv}


def test_extract_last_inventoryinfo_wins():
    inv = ic._extract_last_json_object(MODERN_LOG, '"InventoryInfo"')
    assert inv["SeqId"] == 9  # latest, not the stale SeqId 1
    wc = _map_fields(inv, ic._INVENTORY_FIELDS)
    assert wc == {"common": 152, "uncommon": 121, "mythic": 10, "gold": 5,
                  "gems": 1380, "vault": 25}
    assert "rare" not in wc  # WildCardRares absent -> simply omitted


def test_legacy_inventory_schema_still_maps():
    inv = ic._extract_last_json_object(LEGACY_LOG, "PlayerInventory.GetPlayerInventory")
    wc = _map_fields(inv, ic._LEGACY_INVENTORY_FIELDS)
    assert wc == {"common": 7, "uncommon": 3, "rare": 2, "mythic": 1,
                  "gold": 500, "gems": 200, "vault": 40}


def test_missing_marker_returns_none():
    assert ic._extract_last_json_object("no inventory here", '"InventoryInfo"') is None


def test_wildcard_only_ingest_preserves_collection_source(monkeypatch):
    """A modern (owned-card-less) log import must not clobber a memory-export collection's
    provenance, nor delete its rows -- it only refreshes wildcards."""
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO collection(grp_id, count) VALUES (12345, 4)")
    db.set_meta(conn, "collection_source", "memory-export:mtga_collection.json")
    conn.commit()

    monkeypatch.setattr(ic, "_read_log", lambda: (MODERN_LOG, "Player.log"))
    res = ic.ingest(conn)

    assert res.cards_written == 0 and res.wildcards_written == 6
    # Owned-card provenance and rows are untouched; wildcard provenance tracked separately.
    assert db.get_meta(conn, "collection_source") == "memory-export:mtga_collection.json"
    assert db.get_meta(conn, "wildcards_source") == "Player.log"
    assert conn.execute("SELECT count FROM collection WHERE grp_id = 12345").fetchone()[0] == 4
