"""Enrich the `cards` table with Scryfall data.

Scryfall's ``arena_id`` equals MTGA's ``GrpId``, so we can attach oracle text, mana cost,
prices, legalities and images to cards we already loaded from the MTGA catalog.

We use Scryfall's bulk-data API (no key required). The ``default_cards`` dump is served as
gzip-compressed JSONL (one card object per line), so we cache the ``.jsonl.gz`` locally and
stream it line by line to keep memory flat, only re-downloading when Scryfall reports a
newer ``updated_at``.
"""

from __future__ import annotations

import gzip
import json
import sqlite3

import httpx

from . import db, paths

_BULK_INDEX_URL = "https://api.scryfall.com/bulk-data"
_BULK_KIND = "default_cards"
# Scryfall asks clients to identify themselves and accept JSON.
_HEADERS = {
    "User-Agent": "mtga-mcp/0.1 (local collection tool)",
    "Accept": "application/json",
}


def _bulk_info() -> tuple[str, str]:
    """Return (jsonl_download_uri, updated_at) for the default_cards bulk dataset."""
    resp = httpx.get(_BULK_INDEX_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    for entry in resp.json()["data"]:
        if entry.get("type") == _BULK_KIND:
            return entry["jsonl_download_uri"], entry["updated_at"]
    raise RuntimeError(f"Scryfall bulk dataset '{_BULK_KIND}' not found")


def _download(url: str) -> None:
    """Download the gzip-compressed JSONL dump to the local cache."""
    paths.ensure_data_dir()
    tmp = paths.SCRYFALL_CACHE.with_suffix(".part")
    with httpx.stream("GET", url, headers=_HEADERS, timeout=None, follow_redirects=True) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
    tmp.replace(paths.SCRYFALL_CACHE)


def _row_from_card(card: dict) -> dict | None:
    """Map a Scryfall card object to our update columns, keyed by arena_id."""
    arena_id = card.get("arena_id")
    if not arena_id:
        return None

    # Double-faced cards keep text/mana/image on card_faces; fall back to the front face.
    faces = card.get("card_faces") or []
    front = faces[0] if faces else {}

    image = (card.get("image_uris") or front.get("image_uris") or {}).get("normal")
    oracle = card.get("oracle_text")
    if not oracle and faces:
        oracle = " // ".join(f.get("oracle_text", "") for f in faces).strip(" /")
    mana_cost = card.get("mana_cost") or front.get("mana_cost")

    legalities = card.get("legalities") or {}
    prices = card.get("prices") or {}
    usd = prices.get("usd")

    return {
        "grp_id": arena_id,
        "color_identity": "".join(card.get("color_identity") or []),
        "type_line": card.get("type_line"),
        "mana_cost": mana_cost,
        "cmc": card.get("cmc"),
        "oracle_text": oracle,
        "keywords": ",".join(card.get("keywords") or []),
        "prices_usd": float(usd) if usd else None,
        "legal_standard": legalities.get("standard"),
        "legal_pioneer": legalities.get("pioneer"),
        "legal_explorer": legalities.get("explorer"),
        "legal_historic": legalities.get("historic"),
        "image_uri": image,
        "scryfall_id": card.get("id"),
    }


_UPDATE_SQL = """
UPDATE cards SET
    color_identity = :color_identity,
    type_line = :type_line,
    mana_cost = :mana_cost,
    cmc = :cmc,
    oracle_text = :oracle_text,
    keywords = :keywords,
    prices_usd = :prices_usd,
    legal_standard = :legal_standard,
    legal_pioneer = :legal_pioneer,
    legal_explorer = :legal_explorer,
    legal_historic = :legal_historic,
    image_uri = :image_uri,
    scryfall_id = :scryfall_id
WHERE grp_id = :grp_id
"""


def ingest(conn: sqlite3.Connection, force: bool = False) -> int:
    """Download (if stale) and apply Scryfall enrichment. Returns rows updated."""
    download_uri, updated_at = _bulk_info()
    cached_at = db.get_meta(conn, "scryfall_updated_at")
    if force or not paths.SCRYFALL_CACHE.exists() or cached_at != updated_at:
        _download(download_uri)

    updated = 0
    with conn, gzip.open(paths.SCRYFALL_CACHE, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            card = json.loads(line)
            row = _row_from_card(card)
            if row is None:
                continue
            cur = conn.execute(_UPDATE_SQL, row)
            updated += cur.rowcount
        db.set_meta(conn, "scryfall_updated_at", updated_at)
    return updated
