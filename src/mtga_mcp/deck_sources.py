"""Fetch decklists from text, deck-hosting APIs, or (best-effort) a meta site.

Durable, ToS-clean paths: pasted text, and the public JSON APIs of Archidekt and Moxfield.
The MTGGoldfish path is a best-effort scraper gated behind an explicit flag — the meta sites
block automated access (Cloudflare, robots ai-train=no), so it may fail and callers should
fall back to pasting the Arena export.

Moxfield's public ``search-sfw`` endpoint additionally gives a *bulk* source: the top N decks
for a format, ranked by views/likes/recency, with no auth or signup needed (``search_moxfield``).
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
    meta_share: float | None = None  # 0..1 fraction of the field, when the source reports one
    win_rate: float | None = None  # 0..1, when the source reports one
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


_MOXFIELD_SORTS = {"updated", "views", "likes", "created"}


def search_moxfield(fmt: str | None, *, count: int = 25, sort: str = "updated") -> list[FetchedDeck]:
    """Top N public Moxfield decks for a format, ranked by ``sort`` (views/likes/updated/created).

    One search call lists candidates (name/id only), then each is fetched individually via
    ``_from_moxfield`` for its actual card list -- a failed individual fetch is skipped rather
    than aborting the whole batch.
    """
    if sort not in _MOXFIELD_SORTS:
        raise ValueError(f"Unknown sort {sort!r}. Choose from: {', '.join(sorted(_MOXFIELD_SORTS))}")
    count = max(1, min(count, 100))  # 100 is search-sfw's own page-size ceiling
    params = {"pageSize": count, "pageNumber": 1, "sortType": sort, "sortDirection": "descending"}
    if fmt:
        params["fmt"] = fmt.lower().replace(" ", "")
    resp = httpx.get(
        "https://api2.moxfield.com/v2/decks/search-sfw", params=params, headers=_HEADERS, timeout=30,
    )
    resp.raise_for_status()
    results: list[FetchedDeck] = []
    for entry in resp.json().get("data", []):
        url = entry.get("publicUrl")
        if not url:
            continue
        try:
            results.append(_from_moxfield(url))
        except (httpx.HTTPError, ValueError):
            continue
    return results


# aetherhub.com's robots.txt places no restrictions at all (unlike the other meta sites),
# and its listing pages are plain server-rendered HTML with a no-auth plain-text export
# endpoint per deck -- so unlike `from_scrape` below, this isn't gated behind an opt-in flag.
# Formats below get a Bo1/Bo3 split ("Standard-BO1" / "Traditional-Standard"); these three
# don't (single game, no best-of).
_AETHERHUB_SINGLE_SLUGS = {"brawl": "Brawl", "historicbrawl": "Historic-Brawl", "limited": "Limited"}
_AETHERHUB_BO_FORMATS = {"standard", "historic", "explorer", "alchemy", "timeless"}
_AETHERHUB_ROW = re.compile(r'/Metagame/[^"]+/Deck/[\w-]+-(?P<id>\d+)"><b>(?P<name>[^<]+)</b>')
# Each row's own "NN% Win Rate: W Wins - L Losses" cell, in the same document order as the
# rows above -- zipped together per page rather than folded into one regex, since the two
# live in separate <td>s (see the matching pattern in search_aetherhub_meta above).
_AETHERHUB_WIN_RATE = re.compile(r'(\d+)% Win Rate: \d+ Wins - \d+ Losses')


def _aetherhub_slug(fmt: str | None, best_of: int | None) -> str:
    norm = (fmt or "standard").strip().lower().replace(" ", "")
    if norm in _AETHERHUB_SINGLE_SLUGS:
        return _AETHERHUB_SINGLE_SLUGS[norm]
    base = norm.capitalize() if norm in _AETHERHUB_BO_FORMATS else (fmt or "Standard").strip().title()
    return f"Traditional-{base}" if best_of == 3 else f"{base}-BO1"


def search_aetherhub(fmt: str | None, *, best_of: int | None = None, count: int = 25) -> list[FetchedDeck]:
    """Top N decks from aetherhub.com's metagame listing for a format (newest first), each
    tagged with its own win rate straight off the listing.

    Pages the listing (~75 rows/page) for (id, name, win_rate) triples, then fetches each
    deck's plain-text export individually -- a failed individual fetch is skipped rather than
    aborting the whole batch.
    """
    slug = _aetherhub_slug(fmt, best_of)
    found: list[tuple[str, str, str]] = []
    page = 1
    while len(found) < count and page <= 20:
        resp = httpx.get(
            f"https://aetherhub.com/MTGA-Decks/{slug}/", params={"p": page},
            headers=_HEADERS, timeout=30,
        )
        if resp.status_code == 404:
            raise ValueError(
                f"No aetherhub.com listing for format {fmt!r} best_of={best_of!r} "
                f"(tried slug {slug!r}). Known: Standard/Historic/Explorer/Alchemy/Timeless "
                "(Bo1 or Bo3), Brawl, Historic Brawl, Limited."
            )
        resp.raise_for_status()
        rows = _AETHERHUB_ROW.findall(resp.text)
        if not rows:
            break
        win_rates = _AETHERHUB_WIN_RATE.findall(resp.text)
        found.extend(
            (deck_id, name, (win_rates[i] if i < len(win_rates) else None))
            for i, (deck_id, name) in enumerate(rows)
        )
        page += 1

    results: list[FetchedDeck] = []
    for deck_id, name, win_rate_pct in found[:count]:
        try:
            resp = httpx.get(
                f"https://aetherhub.com/Deck/MtgoDeckExport/{deck_id}", headers=_HEADERS, timeout=30,
            )
            resp.raise_for_status()
            results.append(FetchedDeck(
                name=name, fmt=fmt, cards=parse_decklist(resp.text), source="aetherhub",
                source_url=f"https://aetherhub.com/Deck/{deck_id}", best_of=best_of,
                win_rate=round(int(win_rate_pct) / 100, 4) if win_rate_pct is not None else None,
            ))
        except (httpx.HTTPError, ValueError):
            continue
    return results


# Matches one archetype tile on /Metagame/<slug>/: its name + the id of its one representative
# decklist (same two-<tr> shape every tile shares -- see search_aetherhub_meta).
_AETHERHUB_ARCHETYPE_ROW = re.compile(
    r'data-color="[^"]*" data-title="(?P<name>[^"]+)" '
    r"onclick=\"window\.location='/Metagame/[^/]+/Deck/[\w-]+-(?P<id>\d+)';\""
)
# Each tile's own "N.NN% of Metagame" figure, in the same document order as the rows above --
# zipped together rather than matched in one combined regex, since the two spans live in
# separate sibling <tr>s and that's more robust to minor markup drift than threading one regex
# across both.
_AETHERHUB_ARCHETYPE_SHARE = re.compile(r'percent-metagame">([\d.]+)% of Metagame')


def search_aetherhub_meta(
    fmt: str | None, *, best_of: int | None = None, count: int = 20
) -> list[FetchedDeck]:
    """Top N archetypes from aetherhub.com's metagame overview for a format, each tagged with
    its real ``meta_share`` (the page's own "% of Metagame" figure, last-30-days by default) and
    imported via its one representative decklist.

    Unlike `search_aetherhub`'s per-deck listing (many individual decks, no strength signal at
    all), this is the source to use when you want `deck best`'s ranking to reflect genuine meta
    strength rather than just buildability.
    """
    slug = _aetherhub_slug(fmt, best_of)
    resp = httpx.get(f"https://aetherhub.com/Metagame/{slug}/", headers=_HEADERS, timeout=30)
    if resp.status_code == 404:
        raise ValueError(
            f"No aetherhub.com metagame overview for format {fmt!r} best_of={best_of!r} "
            f"(tried slug {slug!r}). Known: Standard/Historic/Explorer/Alchemy/Timeless "
            "(Bo1 or Bo3), Brawl, Historic Brawl, Limited."
        )
    resp.raise_for_status()
    rows = _AETHERHUB_ARCHETYPE_ROW.findall(resp.text)
    shares = _AETHERHUB_ARCHETYPE_SHARE.findall(resp.text)

    results: list[FetchedDeck] = []
    for (name, deck_id), share in list(zip(rows, shares))[:count]:
        try:
            resp = httpx.get(
                f"https://aetherhub.com/Deck/MtgoDeckExport/{deck_id}", headers=_HEADERS, timeout=30,
            )
            resp.raise_for_status()
            results.append(FetchedDeck(
                name=name, fmt=fmt, cards=parse_decklist(resp.text), source="aetherhub-meta",
                source_url=f"https://aetherhub.com/Deck/{deck_id}", best_of=best_of,
                meta_share=round(float(share) / 100, 4),
            ))
        except (httpx.HTTPError, ValueError):
            continue
    return results


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
