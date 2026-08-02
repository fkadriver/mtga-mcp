"""Fetch decklists from text, deck-hosting APIs, or (best-effort) a meta site.

Durable, ToS-clean paths: pasted text, and the public JSON APIs of Archidekt and Moxfield.
The MTGGoldfish path is a best-effort scraper gated behind an explicit flag — the meta sites
block automated access (Cloudflare, robots ai-train=no), so it may fail and callers should
fall back to pasting the Arena export.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from .decklist import DeckCard, parse_decklist

# Browser-like, self-identifying UA. Deck-host APIs reject obviously robotic clients.
_HEADERS = {
    "User-Agent": "mtga-mcp/0.2 (personal collection tool; +https://github.com/fkadriver/mtga-mcp)",
    "Accept": "application/json",
}

# Archidekt numeric format ids -> readable names (subset covering MTGA-relevant formats).
_ARCHIDEKT_FORMATS = {
    1: "Standard", 2: "Modern", 3: "Commander", 4: "Legacy", 5: "Vintage", 6: "Pauper",
    13: "Brawl", 15: "Pioneer", 16: "Historic", 18: "Alchemy", 19: "Explorer",
    20: "Historic Brawl", 23: "Timeless",
}


@dataclass
class FetchedDeck:
    name: str
    fmt: str | None
    cards: list[DeckCard]
    source: str
    source_url: str | None = None
    best_of: int | None = None
    extra: dict = field(default_factory=dict)


def from_text(text: str, *, name: str | None = None) -> FetchedDeck:
    return FetchedDeck(
        name=name or "Imported deck", fmt=None, cards=parse_decklist(text), source="text"
    )


def from_url(url: str) -> FetchedDeck:
    if "archidekt.com" in url:
        return _from_archidekt(url)
    if "moxfield.com" in url:
        return _from_moxfield(url)
    raise ValueError(
        f"Unsupported deck host: {url}. Supported: archidekt.com, moxfield.com. "
        "For other sites, paste the Arena/MTGO decklist text instead."
    )


def _get_json(url: str) -> dict:
    resp = httpx.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def _from_archidekt(url: str) -> FetchedDeck:
    m = re.search(r"archidekt\.com/(?:api/)?decks/(\d+)", url)
    if not m:
        raise ValueError(f"Could not find an Archidekt deck id in {url!r}")
    deck_id = m.group(1)
    data = _get_json(f"https://archidekt.com/api/decks/{deck_id}/")

    cards: list[DeckCard] = []
    for entry in data.get("cards", []):
        categories = entry.get("categories") or []
        if "Maybeboard" in categories:
            continue
        board = "side" if "Sideboard" in categories else "main"
        card = entry.get("card", {})
        oracle = (card.get("oracleCard") or {}).get("name") or card.get("name")
        if not oracle:
            continue
        set_code = (card.get("edition") or {}).get("editioncode")
        cards.append(DeckCard(
            quantity=int(entry.get("quantity", 1)), name=oracle,
            set_code=(set_code or "").upper() or None, board=board,
        ))

    fmt_val = data.get("format") or data.get("deckFormat")
    fmt = _ARCHIDEKT_FORMATS.get(fmt_val, str(fmt_val)) if fmt_val else None
    return FetchedDeck(
        name=data.get("name") or f"Archidekt {deck_id}", fmt=fmt, cards=cards,
        source="archidekt", source_url=url,
    )


def _from_moxfield(url: str) -> FetchedDeck:
    m = re.search(r"moxfield\.com/decks/([\w-]+)", url)
    if not m:
        raise ValueError(f"Could not find a Moxfield deck id in {url!r}")
    deck_id = m.group(1)
    data = _get_json(f"https://api2.moxfield.com/v3/decks/all/{deck_id}")

    cards: list[DeckCard] = []
    boards = data.get("boards", {})
    for board_key, board in (("mainboard", "main"), ("sideboard", "side")):
        for entry in (boards.get(board_key, {}).get("cards", {}) or {}).values():
            card = entry.get("card", {})
            name = card.get("name")
            if not name:
                continue
            cards.append(DeckCard(
                quantity=int(entry.get("quantity", 1)), name=name,
                set_code=(card.get("set") or "").upper() or None, board=board,
            ))
    return FetchedDeck(
        name=data.get("name") or f"Moxfield {deck_id}", fmt=data.get("format"),
        cards=cards, source="moxfield", source_url=url,
    )


def from_scrape(url: str, *, enable: bool = False) -> FetchedDeck:
    """Best-effort meta-site scraper (MTGGoldfish). Disabled unless `enable=True`.

    Meta sites actively block automated access, so this is intentionally minimal and fails
    with clear guidance rather than fighting Cloudflare. Prefer paste / API imports.
    """
    if not enable:
        raise PermissionError(
            "Scraping is disabled. Pass allow_scrape=True to attempt it, but note the meta "
            "sites block automated access (robots ai-train=no); pasting the Arena export or "
            "using an Archidekt/Moxfield URL is far more reliable."
        )
    scrape_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html",
    }
    try:
        resp = httpx.get(url, headers=scrape_headers, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(
            f"Scrape failed ({e}). The site likely blocked the request. "
            "Paste the decklist text or use an Archidekt/Moxfield URL instead."
        ) from e

    html = resp.text
    # MTGGoldfish embeds the Arena/txt list in a hidden input/textarea on deck pages.
    m = re.search(r'<textarea[^>]*>(?P<body>.*?)</textarea>', html, re.DOTALL | re.IGNORECASE)
    if not m:
        raise RuntimeError(
            "Could not locate a decklist in the page markup (site layout may have changed). "
            "Paste the Arena export or use an Archidekt/Moxfield URL instead."
        )
    title = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
    name = title.group(1).strip() if title else url
    return FetchedDeck(
        name=name, fmt=None, cards=parse_decklist(m.group("body")),
        source="mtggoldfish", source_url=url,
    )
