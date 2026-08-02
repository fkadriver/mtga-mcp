# mtga-mcp

A local [MCP](https://modelcontextprotocol.io) server for querying your **Magic: The
Gathering Arena** collection in natural language. It ingests MTGA's own card catalog, your
owned cards from the game log, and [Scryfall](https://scryfall.com) card data into a local
SQLite database, then exposes query tools that any MCP client (Claude Desktop, Claude Code)
can call.

Nothing leaves your machine except the one-time Scryfall bulk-data download. There is no
model bundled here — your MCP client supplies the LLM.

## How it works

| Source | What it provides | Location (macOS) |
| --- | --- | --- |
| MTGA card catalog | Every card: name, set, collector #, rarity, colors | `~/Library/Application Support/com.wizards.mtga/Downloads/Raw/Raw_CardDatabase_*.mtga` |
| Player.log | Cards you own + wildcard/currency balances | `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log` |
| Scryfall bulk | Oracle text, mana cost, prices, legalities, images | downloaded from scryfall.com |

The join key is MTGA's `GrpId`, which equals Scryfall's `arena_id`. Everything lands in
`~/.local/share/mtga-mcp/mtga.db` (override with `MTGA_MCP_DATA_DIR`).

> **Paths are macOS-specific** (this project targets a native macOS MTGA install launched
> via Heroic). Adjust `src/mtga_mcp/paths.py` for other platforms.

## Setup

```bash
uv sync
```

### Step 0 — enable MTGA Detailed Logs (required for owned counts)

MTGA only writes your collection to `Player.log` when detailed logging is on:

1. In MTGA: **Settings → Account → check "Detailed Logs (Plugin Support)"**.
2. Restart MTGA and open your **Collection** screen once.

The card catalog and Scryfall data work without this; only *owned quantities* need it.

### Import data

```bash
uv run mtga-mcp import            # runs all three steps
# or selectively:
uv run mtga-mcp import --catalog      # ~19.7k cards from MTGA
uv run mtga-mcp import --collection   # your owned cards + wildcards
uv run mtga-mcp import --scryfall     # enrich (downloads a ~77MB bulk file, cached)
```

Re-run `import --collection` whenever your collection changes; re-run `--scryfall`
occasionally for new sets/prices (it only re-downloads when Scryfall has newer data).

## Use it from an MCP client

### Claude Code

```bash
claude mcp add mtga -- uv --directory /Users/scott/git/mtga-mcp run mtga-mcp serve
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mtga": {
      "command": "uv",
      "args": ["--directory", "/Users/scott/git/mtga-mcp", "run", "mtga-mcp", "serve"]
    }
  }
}
```

Then ask things like:

- "How complete is my collection? Show the per-rarity breakdown and my wildcards."
- "Which rares am I missing from FDN?"
- "List blue instants I own that are Standard-legal."

## Tools exposed

| Tool | Purpose |
| --- | --- |
| `search_cards` | Filter by name / colors / rarity / set / type; optional `owned_only` |
| `owned_cards` | Cards you own (count ≥ 1), same filters |
| `missing_from_set` | Cards in a set you haven't got a playset (4) of, with a `needed` count |
| `collection_summary` | Distinct owned, total copies, per-rarity breakdown, wildcards |
| `query_sql` | Ad-hoc **read-only** `SELECT` over the database |

Database tables: `cards`, `collection(grp_id, count)`, `wildcards(kind, count)`, `meta`.

## Development

```bash
uv run pytest
```

## Not yet implemented (ideas)

Deck-buildability / "wildcards needed to build this decklist", meta-deck imports
(MTGGoldfish/Untapped/AetherHub), live log-watching, non-English card names.
