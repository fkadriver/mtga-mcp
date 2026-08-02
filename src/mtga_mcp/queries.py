"""Reusable read queries over our database.

These functions are shared by the MCP tools (server.py) and the tests. They return plain
Python lists/dicts so they serialize cleanly as MCP tool results. A "playset" in MTGA is
4 copies (except basic lands), so completion logic uses a target of 4.
"""

from __future__ import annotations

import sqlite3

PLAYSET = 4
_RARITY_ORDER = {"mythic": 4, "rare": 3, "uncommon": 2, "common": 1, "basic": 0}

# Columns safe/useful to return for a card. Owned count is joined in as `owned`.
_CARD_COLUMNS = (
    "c.grp_id, c.name, c.set_code, c.collector_number, c.rarity, c.colors, "
    "c.color_identity, c.type_line, c.mana_cost, c.cmc, c.power, c.toughness, "
    "c.oracle_text, c.keywords, c.prices_usd, c.legal_standard, c.image_uri, "
    "COALESCE(col.count, 0) AS owned"
)


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def search_cards(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    colors: str | None = None,
    rarity: str | None = None,
    set_code: str | None = None,
    type_contains: str | None = None,
    owned_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Flexible card search. `colors` matches cards whose color_identity is a subset of
    the given WUBRG letters is *not* applied here; instead we match cards containing all
    given letters in their `colors`. Set/rarity are exact; name/type are substring."""
    where: list[str] = []
    params: list[object] = []
    if name:
        where.append("c.name LIKE ?")
        params.append(f"%{name}%")
    if rarity:
        where.append("c.rarity = ?")
        params.append(rarity.lower())
    if set_code:
        where.append("c.set_code = ?")
        params.append(set_code.upper())
    if type_contains:
        where.append("c.type_line LIKE ?")
        params.append(f"%{type_contains}%")
    if colors:
        for letter in colors.upper():
            where.append("c.colors LIKE ?")
            params.append(f"%{letter}%")
    if owned_only:
        where.append("COALESCE(col.count, 0) > 0")

    sql = (
        f"SELECT {_CARD_COLUMNS} FROM cards c "
        "LEFT JOIN collection col ON col.grp_id = c.grp_id "
    )
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY c.name LIMIT ?"
    params.append(limit)
    return _rows(conn.execute(sql, params))


def owned_cards(conn: sqlite3.Connection, *, limit: int = 200, **filters) -> list[dict]:
    """Cards the player owns at least one copy of (thin wrapper over search_cards)."""
    return search_cards(conn, owned_only=True, limit=limit, **filters)


def missing_from_set(
    conn: sqlite3.Connection, set_code: str, *, rarity: str | None = None
) -> list[dict]:
    """Cards in `set_code` where the player owns fewer than a playset (4). Includes a
    `needed` field = how many more copies to complete the playset."""
    params: list[object] = [set_code.upper()]
    rarity_clause = ""
    if rarity:
        rarity_clause = "AND c.rarity = ? "
        params.append(rarity.lower())
    sql = (
        f"SELECT {_CARD_COLUMNS}, ({PLAYSET} - COALESCE(col.count, 0)) AS needed "
        "FROM cards c LEFT JOIN collection col ON col.grp_id = c.grp_id "
        "WHERE c.set_code = ? " + rarity_clause +
        f"AND c.rarity != 'basic' AND COALESCE(col.count, 0) < {PLAYSET} "
        "ORDER BY c.rarity, c.name"
    )
    return _rows(conn.execute(sql, params))


def collection_summary(conn: sqlite3.Connection) -> dict:
    """High-level stats: distinct owned cards, total copies, breakdown by rarity,
    playset completion, and wildcard balances."""
    by_rarity = _rows(
        conn.execute(
            "SELECT c.rarity, "
            "COUNT(DISTINCT CASE WHEN col.count > 0 THEN c.grp_id END) AS distinct_owned, "
            "COALESCE(SUM(col.count), 0) AS copies, "
            "COUNT(DISTINCT c.grp_id) AS distinct_total "
            "FROM cards c LEFT JOIN collection col ON col.grp_id = c.grp_id "
            "GROUP BY c.rarity"
        )
    )
    by_rarity.sort(key=lambda r: _RARITY_ORDER.get(r["rarity"], -1), reverse=True)

    totals = conn.execute(
        "SELECT COUNT(*) AS distinct_owned, COALESCE(SUM(count), 0) AS total_copies "
        "FROM collection WHERE count > 0"
    ).fetchone()
    wildcards = {
        r["kind"]: r["count"] for r in conn.execute("SELECT kind, count FROM wildcards")
    }
    return {
        "distinct_owned": totals["distinct_owned"],
        "total_copies": totals["total_copies"],
        "by_rarity": by_rarity,
        "wildcards": wildcards,
    }


def run_readonly_sql(conn: sqlite3.Connection, sql: str, *, limit: int = 200) -> list[dict]:
    """Run a single read-only SELECT (for ad-hoc / LLM-generated queries).

    Rejects anything that isn't a lone SELECT/WITH statement. `conn` should be a
    read-only connection (see db.connect_readonly)."""
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        raise ValueError("Only a single statement is allowed.")
    if not stripped.lower().startswith(("select", "with")):
        raise ValueError("Only SELECT/WITH queries are allowed.")
    cur = conn.execute(stripped)
    rows = [dict(r) for r in cur.fetchmany(limit)]
    return rows
