"""Detect near-duplicate decks pulled from different sources (e.g. the same Standard Mono
White list showing up from both a Moxfield pull and an AetherHub pull).

Rows are never merged or deleted. Instead, a deck within ``_MAX_DIFF`` mainboard cards
(summed absolute quantity difference across non-basic card names, sideboard ignored) of an
*earlier* (lower id) deck in the same format gets its ``dup_of`` pointed at that earlier
deck -- a loose, transitive, single-linkage clustering: B near A, C near B does not require
C to be within ``_MAX_DIFF`` of A directly. ``deck_analysis.best_buildable_deck`` uses
``dup_of`` to collapse each cluster to its single best-scoring member for display.
"""

from __future__ import annotations

import sqlite3

_MAX_DIFF = 2


def _mainboard_counts(conn: sqlite3.Connection, deck_id: int) -> dict[str, int]:
    """Non-basic mainboard card name -> total quantity (duplicates across rows summed)."""
    rows = conn.execute(
        "SELECT dc.card_name AS name, dc.quantity AS qty, c.rarity AS rarity "
        "FROM deck_cards dc LEFT JOIN cards c ON c.grp_id = dc.grp_id "
        "WHERE dc.deck_id = ? AND dc.board = 'main'",
        (deck_id,),
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        if r["rarity"] == "basic":
            continue
        counts[r["name"]] = counts.get(r["name"], 0) + r["qty"]
    return counts


def _diff(a: dict[str, int], b: dict[str, int]) -> int:
    return sum(abs(a.get(k, 0) - b.get(k, 0)) for k in (a.keys() | b.keys()))


def find_duplicate(conn: sqlite3.Connection, deck_id: int) -> int | None:
    """The canonical id of the closest earlier (lower id), same-format deck within
    `_MAX_DIFF` mainboard cards of `deck_id`, or None if nothing matches closely enough."""
    row = conn.execute("SELECT format FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        return None
    fmt = row["format"]
    mine = _mainboard_counts(conn, deck_id)
    if not mine:
        return None

    if fmt is None:
        candidates = conn.execute(
            "SELECT id, dup_of FROM decks WHERE id < ? AND format IS NULL ORDER BY id",
            (deck_id,),
        ).fetchall()
    else:
        candidates = conn.execute(
            "SELECT id, dup_of FROM decks WHERE id < ? AND format = ? COLLATE NOCASE ORDER BY id",
            (deck_id, fmt),
        ).fetchall()

    best_id, best_diff = None, _MAX_DIFF + 1
    for cand in candidates:
        other = _mainboard_counts(conn, cand["id"])
        if not other:
            continue
        d = _diff(mine, other)
        if d <= _MAX_DIFF and d < best_diff:
            best_diff = d
            best_id = cand["dup_of"] or cand["id"]
    return best_id


def link_duplicate(conn: sqlite3.Connection, deck_id: int) -> int | None:
    """Find and persist `deck_id`'s `dup_of`, if it's a near-duplicate of an earlier deck."""
    canonical = find_duplicate(conn, deck_id)
    if canonical is not None:
        conn.execute("UPDATE decks SET dup_of = ? WHERE id = ?", (canonical, deck_id))
        conn.commit()
    return canonical


def backfill(conn: sqlite3.Connection) -> int:
    """Link `dup_of` for every existing deck that doesn't have one yet (oldest first, so
    each deck only ever looks at already-processed, lower-id decks). Returns how many new
    links were made."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM decks WHERE dup_of IS NULL ORDER BY id")]
    return sum(1 for deck_id in ids if link_duplicate(conn, deck_id) is not None)
