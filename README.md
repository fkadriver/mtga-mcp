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

> **Platforms.** Defaults target a **native macOS** MTGA install. On **Linux/NixOS running
> MTGA via Heroic (Wine/Proton)** the files live inside the game's Wine prefix — point the
> tool at them with environment variables (no code changes):
>
> | Env var | What |
> | --- | --- |
> | `MTGA_MCP_PLAYER_LOG` | full path to `Player.log` (prev log is inferred as a sibling) |
> | `MTGA_MCP_RAW_DIR` | dir holding `Raw_CardDatabase_*.mtga` |
> | `MTGA_MCP_UTC_LOG_DIR` | dir of rotating `UTC_Log*.log` files |
> | `MTGA_MCP_DATA_DIR` | where our own DB/caches live (default `~/.local/share/mtga-mcp`) |
>
> Under Heroic these are typically at
> `<prefix>/drive_c/users/<user>/AppData/LocalLow/Wizards Of The Coast/MTGA/…`
> (`Player.log`, and `Downloads/Raw` for the card DB).

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

#### Full collection (recommended): memory-scanner export

Modern MTGA clients no longer log the full owned-card list — `import --collection` only
recovers wildcards plus *new* card grants captured going forward, never cards you already
owned. To get the **complete** collection, dump it from the running client's memory with the
vendored [MTGA-collection-exporter](https://github.com/NthPhantom10/MTGA-collection-exporter)
(under `third_party/`, MIT-licensed, with a macOS SIGBUS fix already applied) and import the
JSON it produces.

With MTGA running and the Collection screen opened once, the wrapper script does the whole
flow — venv bootstrap (first run only), memory scan, and import:

```bash
scripts/export-collection.sh        # interactive: sudo password + anchor cards
```

The scanner is interactive: it prompts for your sudo password, and on the **first run** asks
for a few "anchor" cards — real card names plus the exact quantities you own — which it uses
to locate the collection in memory. Those are cached (`last_anchors.json`), so later runs just
ask `Use these? [Y/n]`. Run it in a real terminal; don't pipe its stdin.

Or run the steps by hand:

```bash
cd third_party/mtga-collection-exporter
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
sudo ./.venv/bin/python mtg.py                   # writes mtga_collection.json (sudo on macOS)
cd ../..
uv run mtga-mcp import-collection third_party/mtga-collection-exporter/mtga_collection.json
```

This **replaces** the `collection` table with an authoritative, point-in-time snapshot
(counts per printing, summed across printings for deck buildability). Re-run it whenever you
want to refresh. On macOS the scanner needs `sudo` (it uses `task_for_pid`); the target MTGA
process must not use the hardened runtime (the Heroic/native build does not).

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
| `import_deck` | Import a decklist from pasted text or an Archidekt/Moxfield URL |
| `list_decks` | List stored decks |
| `deck_gap` | Cards + wildcards needed to complete a deck |
| `craft_priority` | Which cards to craft to unlock the most decks |
| `best_buildable_deck` | Best Bo1/Bo3 deck you could build, meta strength × buildability |
| `delete_deck` | Remove a stored deck |
| `wildcard_history` | Recent history of wildcard/currency balances (from scheduled capture) |

Database tables: `cards`, `collection(grp_id, count)`, `wildcards(kind, count)`, `meta`,
`decks`, `deck_cards`, `inventory_raw`, `inventory_history`.

## Deck buildability

Import meta decks (or your own brews), then ask what you're missing and what to build.

```bash
# Import from pasted Arena/MTGO text (tag it with meta info for ranking):
pbpaste | uv run mtga-mcp deck import --paste --name "Mono-Red" --format Standard \
  --best-of 1 --meta-share 0.18
uv run mtga-mcp deck import --file list.txt --name "Dimir Midrange" --best-of 3 --tier 1

# Import from a deck host with a public API:
uv run mtga-mcp deck import --url https://archidekt.com/decks/1234567
uv run mtga-mcp deck import --url https://www.moxfield.com/decks/AbCdEf

uv run mtga-mcp deck gap "Mono-Red"          # cards + wildcards you still need
uv run mtga-mcp deck best --best-of 1         # best deck you can build right now
uv run mtga-mcp deck craft-priority           # what to craft to unlock the most decks
```

The **north-star** query — *"given my cards and the current meta, what's the best Bo1/Bo3
deck I could build?"* — is `deck best`, which ranks decks by meta strength × how few
wildcards you're missing. Strength comes from the `--tier` / `--meta-share` / `--win-rate`
you supply at import (meta sites don't expose this programmatically); with none supplied it
ranks purely by buildability.

Decks containing cards not legal in their format (rotated or banned, per the Scryfall
legalities) are excluded by default — a "best deck" you can't actually play isn't an answer.
Pass `--include-illegal` to keep them, flagged with `format_legal` and the offending
`illegal_cards`. Formats without legality data (Alchemy/Timeless) are never excluded on those
grounds.

### A note on meta-deck sources

The big meta sites (MTGGoldfish, Untapped, AetherHub, mtgdecks) actively block automated
access — Cloudflare, robots `ai-train=no`, blocked bot user-agents, and blocked export
endpoints. So this tool imports decklists from **pasted text** and **deck-host public APIs**
(Archidekt, Moxfield) instead. A best-effort MTGGoldfish scraper exists behind an explicit
`--allow-scrape` flag but is brittle and may fail; pasting the Arena export (one click in your
browser) is the reliable path.

## Scheduled capture (wildcards + future card deltas)

Modern MTGA clients don't log your full owned-card collection — only `InventoryInfo`
(wildcards/currency, plus a `Changes` delta array that's populated on real acquisition events:
pack opens, precon grants, bundle/voucher redemptions). `Player.log` also rotates, so those
payloads are ephemeral. `mtga-mcp capture` archives every distinct `InventoryInfo` payload
into `inventory_raw` (deduped by content hash — `SeqId` resets every MTGA session, so it's not
a safe dedup key on its own), records a wildcard/currency snapshot in `inventory_history`, and
applies any `Changes[].GrantedCards` entries to the `collection` table. Running it on a
schedule accumulates a timeline and **owned-card deltas** as packs are opened / new sets
release.

**Caveat:** `collection` counts built this way only reflect deltas captured *since capture
started running* — there's no full historical collection to reconcile against, since modern
clients don't log one. A card you owned before capture ran (and haven't been granted again
since) won't show up.

```bash
uv run mtga-mcp capture       # skips instantly if the logs haven't changed since last run
uv run mtga-mcp history       # recent wildcard/currency snapshots
```

`capture` stat-checks the logs and **skips all parsing when they're unchanged** (i.e. when
you're not playing), so a frequent schedule costs almost nothing at idle.

### Run it automatically

**macOS (launchd):** edit `packaging/com.mtga-mcp.capture.plist` (replace `__REPO__` and
`__LOG__`), copy to `~/Library/LaunchAgents/com.mtga-mcp.capture.plist`, then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mtga-mcp.capture.plist
# unload with: launchctl bootout gui/$(id -u)/com.mtga-mcp.capture
```

**Linux/NixOS (systemd user timer):** run `.venv/bin/mtga-mcp capture` from a `*.service` on a
15-minute `*.timer`, with the `MTGA_MCP_*` path vars (above) set in the unit's `Environment=`.
If a path contains spaces (common under Wine prefixes, e.g. `.../Magic The Gathering
Arena/...`), quote the **whole** `VAR=value` assignment — `Environment="VAR=/path with
spaces"` — not just the value, or systemd word-splits it into several broken assignments. See
[`docs/latitude-setup.md`](docs/latitude-setup.md) for a full worked example, including doing
this declaratively via home-manager's `systemd.user.services`/`timers`.

## Sharing findings across machines (Syncthing)

To use one database across several machines (e.g. macOS + a Linux/NixOS laptop), keep the DB
in a synced folder and leave the big regenerable Scryfall cache local:

```bash
export MTGA_MCP_DB_PATH=~/Documents/mtga-mcp/mtga.db   # inside your Syncthing folder
# DATA_DIR (Scryfall cache, ~74 MB) stays local and is NOT synced.
```

Set `MTGA_MCP_DB_PATH` everywhere the tool runs: your shell profile, the capture LaunchAgent
(`EnvironmentVariables`), and the MCP client config (`env` block of the server entry). Then
append `packaging/syncthing.stignore` to the `.stignore` at the root of the synced folder so
SQLite's transient `-journal/-wal/-shm` sidecars aren't synced.

This is safe because **MTGA runs on only one device at a time**, so the two machines never
write the DB concurrently; the ~11 MB `mtga.db` (decks, wildcards, inventory history) syncs as
a single clean file.

## Development

```bash
uv run pytest
```

## Not yet implemented (ideas)

Auto-refreshing meta snapshots, live log-watching, deck similarity/clustering, non-English
card names.
