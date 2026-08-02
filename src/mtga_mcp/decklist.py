"""Parse decklists, resolve card names to our catalog, and store decks.

Decklists come in two common plain-text shapes:

  Arena:  ``4 Sheoldred, the Apocalypse (DMU) 107`` with ``Deck`` / ``Sideboard`` headers
  MTGO:   ``4 Sheoldred, the Apocalypse`` with a blank line separating main from sideboard

Card names in a deck reference a *card*, but our collection is tracked per *printing*
(grp_id). MTGA pools copies of the same card across printings toward the 4-of deck limit and
lets you craft the cheapest-rarity printing, so `resolve_card` aggregates across printings.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

PLAYSET = 4
_RARITY_RANK = {"basic": 0, "common": 1, "uncommon": 2, "rare": 3, "mythic": 4}

# "4 Card Name (SET) 123"  or  "4x Card Name"  or  "4 Card Name"
_LINE_RE = re.compile(
    r"^\s*(\d+)\s*x?\s+(.+?)\s*(?:\((?P<set>[A-Za-z0-9]{2,5})\)\s*(?P<cn>\S+)?)?\s*$"
)
# Section headers seen in Arena/MTGO exports.
_MAIN_HEADERS = {"deck", "commander", "companion", "mainboard", "maindeck"}
_SIDE_HEADERS = {"sideboard"}


@dataclass
class DeckCard:
    quantity: int
    name: str
    set_code: str | None
    board: str  # 'main' or 'side'


@dataclass
class CardInfo:
    """Resolved ownership/craft info for a card *name*."""
    name: str
    matched: bool
    grp_id: int | None = None        # a representative printing (cheapest rarity)
    owned: int = 0                   # min(PLAYSET, copies owned across all printings)
    rarity: str | None = None        # cheapest non-basic printing rarity, for wildcard cost
    is_basic: bool = False


def parse_decklist(text: str) -> list[DeckCard]:
    """Parse Arena or MTGO decklist text into DeckCard rows."""
    cards: list[DeckCard] = []
    board = "main"
    seen_blank = False
    has_headers = any(
        line.strip().lower() in _MAIN_HEADERS | _SIDE_HEADERS for line in text.splitlines()
    )
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            # In header-less MTGO exports, the first blank line starts the sideboard.
            if not has_headers and cards:
                seen_blank = True
            continue
        low = line.lower()
        if low in _MAIN_HEADERS:
            board = "main"
            continue
        if low in _SIDE_HEADERS:
            board = "side"
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        qty = int(m.group(1))
        name = m.group(2).strip()
        set_code = (m.group("set") or "").upper() or None
        cur_board = "side" if (not has_headers and seen_blank) else board
        cards.append(DeckCard(qty, name, set_code, cur_board))
    return cards


def _match_rows(conn: sqlite3.Connection, name: str, set_code: str | None) -> list[sqlite3.Row]:
    """Return catalog rows for a card name, trying several normalizations."""
    candidates = [name]
    if name.startswith("A-"):  # Arena rebalanced prefix
        candidates.append(name[2:])
    if " // " in name:  # split / DFC — try the front face
        candidates.append(name.split(" // ", 1)[0])
    for cand in candidates:
        if set_code:
            rows = conn.execute(
                "SELECT grp_id, rarity, type_line FROM cards "
                "WHERE name = ? COLLATE NOCASE AND set_code = ?",
                (cand, set_code),
            ).fetchall()
            if rows:
                return rows
        rows = conn.execute(
            "SELECT grp_id, rarity, type_line FROM cards WHERE name = ? COLLATE NOCASE",
            (cand,),
        ).fetchall()
        if rows:
            return rows
    return []


def resolve_card(conn: sqlite3.Connection, name: str, set_code: str | None = None) -> CardInfo:
    """Resolve a deck card name to ownership + cheapest-craft info across printings."""
    rows = _match_rows(conn, name, set_code)
    if not rows:
        return CardInfo(name=name, matched=False)

    grp_ids = [r["grp_id"] for r in rows]
    # Cheapest rarity printing decides wildcard cost and the representative grp_id.
    best = min(rows, key=lambda r: _RARITY_RANK.get(r["rarity"] or "mythic", 4))
    rarity = best["rarity"]
    is_basic = rarity == "basic"

    placeholders = ",".join("?" * len(grp_ids))
    owned_row = conn.execute(
        f"SELECT COALESCE(SUM(count), 0) AS owned FROM collection WHERE grp_id IN ({placeholders})",
        grp_ids,
    ).fetchone()
    owned = min(PLAYSET, owned_row["owned"])

    return CardInfo(
        name=name, matched=True, grp_id=best["grp_id"], owned=owned,
        rarity=rarity, is_basic=is_basic,
    )


@dataclass
class StoredDeck:
    deck_id: int
    resolved: int
    unresolved: list[str] = field(default_factory=list)


def store_deck(
    conn: sqlite3.Connection,
    *,
    name: str,
    fmt: str | None,
    cards: list[DeckCard],
    source: str,
    source_url: str | None = None,
    best_of: int | None = None,
    tier: str | None = None,
    meta_share: float | None = None,
    win_rate: float | None = None,
) -> StoredDeck:
    """Insert a deck and its cards (resolving grp_ids). Returns id + unresolved names."""
    unresolved: list[str] = []
    resolved = 0
    with conn:
        cur = conn.execute(
            "INSERT INTO decks(name, format, best_of, tier, meta_share, win_rate, "
            "source, source_url, imported_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (name, fmt, best_of, tier, meta_share, win_rate, source, source_url,
             datetime.now(timezone.utc).isoformat()),
        )
        deck_id = cur.lastrowid
        for dc in cards:
            info = resolve_card(conn, dc.name, dc.set_code)
            if info.matched:
                resolved += 1
            else:
                unresolved.append(dc.name)
            conn.execute(
                "INSERT INTO deck_cards(deck_id, card_name, set_code, quantity, board, grp_id) "
                "VALUES(?,?,?,?,?,?)",
                (deck_id, dc.name, dc.set_code, dc.quantity, dc.board, info.grp_id),
            )
    return StoredDeck(deck_id=deck_id, resolved=resolved, unresolved=unresolved)
