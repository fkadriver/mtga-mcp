"""Tests for parsing MTGA log inventory payloads."""

from __future__ import annotations

from mtga_mcp import ingest_collection as ic

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
