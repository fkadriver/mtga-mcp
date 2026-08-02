"""Buildability analysis over stored decks and the player's collection.

Three capabilities:
  * ``deck_gap``            — cards + wildcards needed to complete one deck
  * ``craft_priority``      — which cards to craft to unlock the most decks
  * ``best_buildable_deck`` — best Bo1/Bo3 deck you can (nearly) build, meta strength × buildability

A constructed deck may run at most 4 copies of a card across main + sideboard (basics
excepted), so needed copies per card name are aggregated across boards and capped at 4.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .decklist import PLAYSET, resolve_card

_WILDCARD_RARITIES = ("common", "uncommon", "rare", "mythic")
_TIER_WEIGHT = {"1": 1.0, "a": 1.0, "2": 0.7, "b": 0.7, "3": 0.45, "c": 0.45, "4": 0.3, "d": 0.3}


def _resolve_deck(conn: sqlite3.Connection, deck: int | str) -> sqlite3.Row | None:
    if isinstance(deck, int) or (isinstance(deck, str) and deck.isdigit()):
        row = conn.execute("SELECT * FROM decks WHERE id = ?", (int(deck),)).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM decks WHERE name = ? COLLATE NOCASE ORDER BY id LIMIT 1", (str(deck),)
    ).fetchone()


def _aggregated_cards(conn: sqlite3.Connection, deck_id: int) -> list[tuple[str, str | None, int]]:
    """Return (name, set_code, total_qty) per distinct card name across main + side."""
    rows = conn.execute(
        "SELECT card_name, MAX(set_code) AS set_code, SUM(quantity) AS qty "
        "FROM deck_cards WHERE deck_id = ? GROUP BY card_name COLLATE NOCASE",
        (deck_id,),
    ).fetchall()
    return [(r["card_name"], r["set_code"], r["qty"]) for r in rows]


def _wildcards_owned(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        r["kind"]: r["count"]
        for r in conn.execute("SELECT kind, count FROM wildcards")
        if r["kind"] in _WILDCARD_RARITIES
    }


def _deck_missing(conn: sqlite3.Connection, deck_id: int) -> tuple[list[dict], list[str]]:
    """Per-card shortfall for a deck. Returns (missing_cards, unresolved_names).

    Each missing card: {name, rarity, needed, owned, missing}. Basics are excluded (free).
    """
    missing: list[dict] = []
    unresolved: list[str] = []
    for name, set_code, total_qty in _aggregated_cards(conn, deck_id):
        info = resolve_card(conn, name, set_code)
        if not info.matched:
            unresolved.append(name)
            continue
        if info.is_basic:
            continue
        needed = min(PLAYSET, total_qty)
        short = max(0, needed - info.owned)
        if short > 0:
            missing.append({
                "name": name, "rarity": info.rarity,
                "needed": needed, "owned": info.owned, "missing": short,
            })
    return missing, unresolved


def deck_gap(conn: sqlite3.Connection, deck: int | str) -> dict:
    """Cards and wildcards needed to complete a deck."""
    row = _resolve_deck(conn, deck)
    if row is None:
        raise ValueError(f"No deck matching {deck!r}")
    missing, unresolved = _deck_missing(conn, row["id"])

    wc_needed: dict[str, int] = defaultdict(int)
    for m in missing:
        if m["rarity"] in _WILDCARD_RARITIES:
            wc_needed[m["rarity"]] += m["missing"]

    total_missing = sum(m["missing"] for m in missing)
    return {
        "deck_id": row["id"],
        "name": row["name"],
        "format": row["format"],
        "best_of": row["best_of"],
        "missing_cards": sorted(missing, key=lambda m: (-m["missing"], m["name"])),
        "wildcards_needed": {r: wc_needed[r] for r in _WILDCARD_RARITIES if wc_needed[r]},
        "wildcards_owned": _wildcards_owned(conn),
        "total_missing_copies": total_missing,
        "buildable": total_missing == 0 and not unresolved,
        "unresolved": unresolved,
    }


def craft_priority(conn: sqlite3.Connection, *, fmt: str | None = None) -> list[dict]:
    """Rank cards to craft by how many decks they unlock (then frequency)."""
    deck_rows = _decks(conn, fmt=fmt)
    # (name, rarity) -> aggregate
    agg: dict[tuple[str, str], dict] = {}
    for d in deck_rows:
        missing, _ = _deck_missing(conn, d["id"])
        # A deck is "completed" by a card only if that card is its sole remaining shortfall.
        sole = missing[0]["name"] if len(missing) == 1 else None
        for m in missing:
            key = (m["name"], m["rarity"])
            entry = agg.setdefault(key, {
                "name": m["name"], "rarity": m["rarity"],
                "copies_needed": 0, "decks_needing": 0, "decks_completed_if_crafted": 0,
            })
            entry["copies_needed"] += m["missing"]
            entry["decks_needing"] += 1
            if sole == m["name"]:
                entry["decks_completed_if_crafted"] += 1
    ranked = sorted(
        agg.values(),
        key=lambda e: (-e["decks_completed_if_crafted"], -e["decks_needing"], -e["copies_needed"]),
    )
    return ranked


def _decks(conn: sqlite3.Connection, *, best_of: int | None = None, fmt: str | None = None):
    where, params = [], []
    if fmt:
        where.append("format = ? COLLATE NOCASE")
        params.append(fmt)
    if best_of is not None:
        # A deck with best_of NULL is valid for either; match it too.
        where.append("(best_of = ? OR best_of IS NULL)")
        params.append(best_of)
    sql = "SELECT * FROM decks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql, params).fetchall()


def _strength(row: sqlite3.Row) -> float:
    """A 0..1-ish strength signal from whatever meta metadata is available."""
    if row["meta_share"] is not None:
        return float(row["meta_share"])
    if row["win_rate"] is not None:
        return float(row["win_rate"])
    if row["tier"]:
        return _TIER_WEIGHT.get(str(row["tier"]).strip().lower(), 0.5)
    return 1.0  # unknown strength: rank purely on buildability


def best_buildable_deck(
    conn: sqlite3.Connection,
    *,
    best_of: int | None = None,
    fmt: str | None = None,
    max_wildcards: int | None = None,
) -> list[dict]:
    """Rank decks by meta strength × buildability. Answers the north-star question:
    'given my cards and the meta, what's the best Bo1/Bo3 deck I could build?'"""
    results: list[dict] = []
    for d in _decks(conn, best_of=best_of, fmt=fmt):
        gap = deck_gap(conn, d["id"])
        total_wc = sum(gap["wildcards_needed"].values())
        if max_wildcards is not None and total_wc > max_wildcards:
            continue
        strength = _strength(d)
        buildability = 1.0 / (1.0 + total_wc)
        results.append({
            "deck_id": d["id"],
            "name": d["name"],
            "format": d["format"],
            "best_of": d["best_of"],
            "tier": d["tier"],
            "meta_share": d["meta_share"],
            "strength": round(strength, 4),
            "wildcards_needed_total": total_wc,
            "wildcards_needed": gap["wildcards_needed"],
            "buildable_now": gap["buildable"],
            "score": round(strength * buildability, 4),
        })
    results.sort(key=lambda r: (-r["score"], r["wildcards_needed_total"]))
    return results
