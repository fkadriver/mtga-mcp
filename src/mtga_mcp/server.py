"""MCP server exposing MTGA collection queries as tools.

Run with `mtga-mcp serve` (stdio transport). Point an MCP client (Claude Desktop,
Claude Code, ...) at that command. Tools are thin wrappers over queries.py; each opens a
fresh SQLite connection so the server stays stateless and safe across concurrent calls.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db, queries

mcp = MCPServer(
    name="mtga",
    version="0.1.0",
    instructions=(
        "Query the user's Magic: The Gathering Arena collection. Card data comes from "
        "MTGA's catalog enriched with Scryfall; owned counts come from the player's log. "
        "A playset is 4 copies. Use search_cards/owned_cards/missing_from_set/"
        "collection_summary for structured queries, or query_sql for anything else."
    ),
)


@mcp.tool(description="Search cards by name/colors/rarity/set/type. colors is WUBRG "
                      "letters; set_code is the 3-letter set. Set owned_only=true to "
                      "restrict to cards the player owns.")
def search_cards(
    name: str | None = None,
    colors: str | None = None,
    rarity: str | None = None,
    set_code: str | None = None,
    type_contains: str | None = None,
    owned_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        return queries.search_cards(
            conn, name=name, colors=colors, rarity=rarity, set_code=set_code,
            type_contains=type_contains, owned_only=owned_only, limit=limit,
        )


@mcp.tool(description="List cards the player owns (owned count >= 1), with the same "
                      "optional filters as search_cards.")
def owned_cards(
    name: str | None = None,
    colors: str | None = None,
    rarity: str | None = None,
    set_code: str | None = None,
    type_contains: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        return queries.owned_cards(
            conn, name=name, colors=colors, rarity=rarity, set_code=set_code,
            type_contains=type_contains, limit=limit,
        )


@mcp.tool(description="Cards in a set the player hasn't completed a playset (4) of. "
                      "Returns a 'needed' count per card. Optional rarity filter.")
def missing_from_set(set_code: str, rarity: str | None = None) -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        return queries.missing_from_set(conn, set_code, rarity=rarity)


@mcp.tool(description="Collection overview: distinct owned, total copies, per-rarity "
                      "breakdown, and wildcard/currency balances.")
def collection_summary() -> dict[str, Any]:
    with db.connect_readonly() as conn:
        return queries.collection_summary(conn)


@mcp.tool(description="Run an ad-hoc read-only SELECT against the collection database. "
                      "Tables: cards, collection(grp_id,count), wildcards(kind,count). "
                      "Join collection on cards.grp_id for owned counts.")
def query_sql(sql: str, limit: int = 200) -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        return queries.run_readonly_sql(conn, sql, limit=limit)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
