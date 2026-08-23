"""Anchor cards for the memory scan.

An "anchor" is a card you own with an exact quantity: the scanner searches MTGA's memory for
the 32-bit ``(arena_id, quantity)`` pair to locate the collection block. Rather than make you
type anchors by hand, we propose a handful pulled from your last-imported `collection` (rares
and mythics, whose counts are stable) and let you confirm or adjust them -- the quantities must
still match what you own *right now*, so an edit path matters if you've opened packs since.

On a first-ever export the `collection` table is empty, so there's nothing to propose and you
enter anchors manually (resolved against the `cards` catalog).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, List, Optional

MIN_ANCHORS = 3
DEFAULT_WANT = 5


@dataclass
class Anchor:
    arena_id: int
    quantity: int
    name: str


def candidates_from_db(conn: sqlite3.Connection, want: int = DEFAULT_WANT) -> List[Anchor]:
    """Propose anchor cards from the owned collection: rares/mythics first, then anything.

    Higher owned counts are preferred (a `(id, 4)` pair is more distinctive in memory than a
    `(id, 1)` that many singletons share), then name for stability.

    Alchemy *rebalanced* printings (name-prefixed ``A-``) are excluded: they sort first
    alphabetically and would otherwise crowd out every proposal, but their digital-only grpids
    make unreliable memory anchors.
    """
    rows = conn.execute(
        """
        SELECT co.grp_id AS grp_id, c.name AS name, co.count AS count,
               CASE c.rarity WHEN 'mythic' THEN 0 WHEN 'rare' THEN 1 ELSE 2 END AS rk
        FROM collection co
        JOIN cards c ON c.grp_id = co.grp_id
        WHERE co.count BETWEEN 1 AND 4 AND c.name IS NOT NULL AND c.name != ''
              AND c.name NOT LIKE 'A-%'
        ORDER BY rk, count DESC, name
        LIMIT ?
        """,
        (want,),
    ).fetchall()
    return [Anchor(arena_id=r["grp_id"], quantity=r["count"], name=r["name"]) for r in rows]


def resolve_card(conn: sqlite3.Connection, query: str) -> Optional[Anchor]:
    """Resolve a typed card name to an Anchor (quantity 0), preferring an owned printing."""
    q = (query or "").strip()
    if not q:
        return None
    row = conn.execute(
        """
        SELECT c.grp_id AS grp_id, c.name AS name,
               COALESCE((SELECT count FROM collection WHERE grp_id = c.grp_id), 0) AS owned
        FROM cards c
        WHERE c.name = ? COLLATE NOCASE
        ORDER BY owned DESC, c.grp_id
        LIMIT 1
        """,
        (q,),
    ).fetchone()
    if not row:
        print(f"    ✗ '{query}' not found in the catalog.")
        return None
    return Anchor(arena_id=row["grp_id"], quantity=0, name=row["name"])


def _ask_quantity(name: str, input_fn: Callable[[str], str]) -> Optional[int]:
    raw = input_fn(f"    quantity of '{name}' you own (1-400): ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 400):
        print("    ✗ quantity must be 1-400.")
        return None
    return int(raw)


def choose_anchors(
    conn: sqlite3.Connection,
    *,
    want: int = DEFAULT_WANT,
    min_anchors: int = MIN_ANCHORS,
    input_fn: Callable[[str], str] = input,
) -> List[Anchor]:
    """Interactively confirm/edit anchor cards. Returns [] if the user quits.

    `input_fn` is injectable so the flow can be unit-tested with a scripted sequence.
    """
    anchors: List[Anchor] = candidates_from_db(conn, want)

    while True:
        print()
        if anchors:
            print("Proposed anchor cards (from your last imported collection):")
            for i, a in enumerate(anchors, 1):
                print(f"  {i}. {a.name}  x{a.quantity}")
            print("These must match what you own RIGHT NOW.")
        else:
            print("No collection on record — add at least "
                  f"{min_anchors} cards you own (name + exact quantity).")
        print("Commands: [Enter]=accept  e<n>=edit qty  d<n>=delete  a=add  q=quit")

        choice = input_fn("> ").strip().lower()

        if choice in ("", "y", "yes"):
            if len(anchors) >= min_anchors:
                return anchors
            print(f"Need at least {min_anchors} anchors.")
            continue
        if choice in ("q", "quit"):
            return []
        if choice in ("a", "add"):
            anchor = resolve_card(conn, input_fn("    card name: "))
            if anchor is None:
                continue
            qty = _ask_quantity(anchor.name, input_fn)
            if qty is None:
                continue
            anchor.quantity = qty
            anchors.append(anchor)
            continue
        if choice and choice[0] in ("e", "d") and choice[1:].strip().isdigit():
            idx = int(choice[1:].strip()) - 1
            if not (0 <= idx < len(anchors)):
                print("    ✗ no such row.")
                continue
            if choice[0] == "d":
                removed = anchors.pop(idx)
                print(f"    removed {removed.name}.")
            else:
                qty = _ask_quantity(anchors[idx].name, input_fn)
                if qty is not None:
                    anchors[idx].quantity = qty
            continue

        print("    ✗ unrecognized command.")
