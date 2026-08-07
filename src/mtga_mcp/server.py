"""MCP server exposing MTGA collection queries as tools.

Run with `mtga-mcp serve` (stdio transport). Point an MCP client (Claude Desktop,
Claude Code, ...) at that command. Tools are thin wrappers over queries.py; each opens a
fresh SQLite connection so the server stays stateless and safe across concurrent calls.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db, deck_analysis, deck_sources, decklist, queries

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


@mcp.tool(description="Import a decklist and store it for buildability analysis. Provide "
                      "exactly one of `text` (pasted Arena/MTGO list) or `url` (Archidekt or "
                      "Moxfield). Optional meta metadata: best_of (1 or 3), tier, meta_share "
                      "(0..1) feed the best_buildable_deck ranking. Set allow_scrape=true to "
                      "attempt an MTGGoldfish URL (unreliable). Returns the deck id and any "
                      "card names that couldn't be matched.")
def import_deck(
    text: str | None = None,
    url: str | None = None,
    name: str | None = None,
    format: str | None = None,
    best_of: int | None = None,
    tier: str | None = None,
    meta_share: float | None = None,
    allow_scrape: bool = False,
) -> dict[str, Any]:
    if bool(text) == bool(url):
        raise ValueError("Provide exactly one of `text` or `url`.")
    if url and "mtggoldfish.com" in url:
        fetched = deck_sources.from_scrape(url, enable=allow_scrape)
    elif url:
        fetched = deck_sources.from_url(url)
    else:
        fetched = deck_sources.from_text(text, name=name)
    with db.connect() as conn:
        stored = decklist.store_deck(
            conn, name=name or fetched.name, fmt=format or fetched.fmt,
            cards=fetched.cards, source=fetched.source, source_url=fetched.source_url,
            best_of=best_of if best_of is not None else fetched.best_of,
            tier=tier, meta_share=meta_share,
        )
    return {
        "deck_id": stored.deck_id, "name": name or fetched.name,
        "source": fetched.source, "cards_resolved": stored.resolved,
        "unresolved": stored.unresolved,
    }


@mcp.tool(description="List stored decks with format, best_of, tier and source.")
def list_decks() -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        rows = conn.execute(
            "SELECT id, name, format, best_of, tier, meta_share, source, imported_at "
            "FROM decks ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool(description="Cards and wildcards needed to complete a deck (by id or name), given "
                      "your collection. Basics are excluded; copies are capped at a playset (4).")
def deck_gap(deck: str) -> dict[str, Any]:
    with db.connect_readonly() as conn:
        return deck_analysis.deck_gap(conn, deck)


@mcp.tool(description="Across all stored decks (optionally one format), rank cards to craft by "
                      "how many decks they would unlock, then by how many decks need them.")
def craft_priority(format: str | None = None) -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        return deck_analysis.craft_priority(conn, fmt=format)


@mcp.tool(description="The best deck you could build given your cards and the meta. Ranks stored "
                      "decks by meta strength x buildability. Filter by best_of (1 or 3) and "
                      "format; max_wildcards hides decks needing more than that many wildcards.")
def best_buildable_deck(
    best_of: int | None = None,
    format: str | None = None,
    max_wildcards: int | None = None,
) -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        return deck_analysis.best_buildable_deck(
            conn, best_of=best_of, fmt=format, max_wildcards=max_wildcards
        )


@mcp.tool(description="Delete a stored deck by id or name.")
def delete_deck(deck: str) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM decks WHERE id = ? OR name = ? COLLATE NOCASE",
            (deck if str(deck).isdigit() else -1, str(deck)),
        ).fetchone()
        if not row:
            raise ValueError(f"No deck matching {deck!r}")
        conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (row["id"],))
        conn.execute("DELETE FROM decks WHERE id = ?", (row["id"],))
        conn.commit()
        return {"deleted": row["id"]}


@mcp.tool(description="Recent history of your wildcard and currency balances (one row per "
                      "capture), newest first. Populated by `mtga-mcp capture`, which runs on a "
                      "schedule to track changes over time as new sets release.")
def wildcard_history(limit: int = 20) -> list[dict[str, Any]]:
    with db.connect_readonly() as conn:
        rows = conn.execute(
            "SELECT captured_at, common, uncommon, rare, mythic, gold, gems, vault "
            "FROM inventory_history ORDER BY captured_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
