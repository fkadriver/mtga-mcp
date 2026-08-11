"""Enrich the `cards` table with Scryfall data.

Scryfall's ``arena_id`` equals MTGA's ``GrpId``, so we can attach oracle text, mana cost,
prices, legalities and images to cards we already loaded from the MTGA catalog.

We use Scryfall's bulk-data API (no key required). The ``default_cards`` dump is served as
gzip-compressed JSONL (one card object per line), so we cache the ``.jsonl.gz`` locally and
stream it line by line to keep memory flat, only re-downloading when Scryfall reports a
newer ``updated_at``.

Not every MTGA printing carries a Scryfall ``arena_id`` (older paper reprints MTGA brought in
via Anthologies/Jumpstart, etc.), so an arena_id-only join leaves ~7% of the catalog
un-enriched. A second pass fills those in by **name + set**, falling back to **name only**: the
fields we attach (type line, oracle text, mana cost, legalities, colour identity, keywords) are
card-level and identical across printings, so any printing of the same-named card is a correct
source for them. Prices/image may then come from a different printing, which is acceptable for
these otherwise-metadata-less cards. Alchemy rebalanced cards (``A-...``) are intentionally left
unmatched -- their oracle text differs from the paper card, so name-matching would be wrong.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from collections import defaultdict
from typing import Iterable

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


def _scryfall_fields(card: dict) -> dict:
    """Map a Scryfall card object to our enrichment columns (everything but grp_id)."""
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


def _row_from_card(card: dict) -> dict | None:
    """Map a Scryfall card object to our update columns, keyed by arena_id (or None)."""
    arena_id = card.get("arena_id")
    if not arena_id:
        return None
    return {"grp_id": arena_id, **_scryfall_fields(card)}


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


def _iter_cards() -> Iterable[dict]:
    """Stream card objects out of the cached gzip-compressed JSONL dump."""
    with gzip.open(paths.SCRYFALL_CACHE, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            yield json.loads(line)


def _apply_arena_matches(conn: sqlite3.Connection, cards: Iterable[dict]) -> int:
    """Enrich cards whose grp_id equals a Scryfall arena_id. Returns rows updated."""
    updated = 0
    for card in cards:
        row = _row_from_card(card)
        if row is not None:
            updated += conn.execute(_UPDATE_SQL, row).rowcount
    return updated


def _apply_name_fallback(conn: sqlite3.Connection, cards: Iterable[dict]) -> int:
    """Enrich still-unmatched cards by name+set, falling back to name only.

    Runs after the arena_id pass; only touches cards still lacking a scryfall_id. An exact
    (name, set) printing wins over a name-only match, so prices/image stay as close to the
    MTGA printing as the bulk data allows.
    """
    name_set_idx: dict[tuple[str, str], list[int]] = defaultdict(list)
    name_idx: dict[str, list[int]] = defaultdict(list)
    for grp_id, name, set_code in conn.execute(
        "SELECT grp_id, name, set_code FROM cards WHERE scryfall_id IS NULL"
    ):
        n = (name or "").lower()
        name_set_idx[(n, (set_code or "").lower())].append(grp_id)
        name_idx[n].append(grp_id)
    if not name_idx:
        return 0

    # grp_id -> (priority, fields); priority 2 = name+set, 1 = name-only.
    best: dict[int, tuple[int, dict]] = {}
    for card in cards:
        name = card.get("name")
        if not name or card.get("id") is None:
            continue
        n = name.lower()
        exact = name_set_idx.get((n, (card.get("set") or "").lower()))
        loose = name_idx.get(n)
        if not exact and not loose:
            continue
        fields = _scryfall_fields(card)
        for grp in exact or ():
            best[grp] = (2, fields)
        for grp in loose or ():
            if best.get(grp, (0, None))[0] < 1:
                best[grp] = (1, fields)

    updated = 0
    for grp, (_prio, fields) in best.items():
        updated += conn.execute(_UPDATE_SQL, {"grp_id": grp, **fields}).rowcount
    return updated


def ingest(conn: sqlite3.Connection, force: bool = False) -> int:
    """Download (if stale) and apply Scryfall enrichment. Returns rows updated."""
    download_uri, updated_at = _bulk_info()
    cached_at = db.get_meta(conn, "scryfall_updated_at")
    if force or not paths.SCRYFALL_CACHE.exists() or cached_at != updated_at:
        _download(download_uri)

    with conn:
        updated = _apply_arena_matches(conn, _iter_cards())
        updated += _apply_name_fallback(conn, _iter_cards())
        db.set_meta(conn, "scryfall_updated_at", updated_at)
    return updated
