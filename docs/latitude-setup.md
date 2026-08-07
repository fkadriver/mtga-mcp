# Latitude (NixOS + Heroic) setup checklist

Ordered steps to bring up **mtga-mcp** + **mtga-replay-coach** on the Linux/NixOS laptop
("Latitude"), where MTGA runs via Heroic (Wine/Proton). Fill in the `<PLACEHOLDERS>` from
step 1. macOS is already configured; this only covers the Linux side.

Assumes both repos are cloned (e.g. `~/git/mtga-mcp`, `~/git/mtga-replay-coach`) and `uv` is
on `PATH`.

## 1. Discover the Wine-prefix paths

With MTGA running and Detailed Logs on, run:

```bash
echo "PLAYER_LOG:"; find ~ -name 'Player.log' -path '*Wizards*MTGA*' 2>/dev/null
echo "RAW_DIR (card db):"; find ~ -iname 'Raw_CardDatabase_*.mtga' 2>/dev/null | sed 's#/[^/]*$##' | sort -u
echo "UTC_LOG_DIR:"; find ~ -type d -path '*Wizards*MTGA*' -name 'Logs' 2>/dev/null
echo "SYNCED DB present?"; ls -lh ~/Documents/mtga-mcp/mtga.db 2>/dev/null || echo "  not synced yet"
```

Record:
- `<PLAYER_LOG>`   – full path to `Player.log`
- `<RAW_DIR>`      – directory containing `Raw_CardDatabase_*.mtga`
- `<UTC_LOG_DIR>`  – the `Logs` dir (may be absent on Linux; that's fine)
- `<REPO_MTGA_MCP>`, `<REPO_COACH>` – the two clone paths
- `<UV>` – output of `command -v uv`
- DB lives at `~/Documents/mtga-mcp/mtga.db` (synced by Syncthing)

## 2. Environment variables

Put these where interactive shells see them (`~/.profile` / your NixOS shell config). They
are also set explicitly in the systemd unit (step 4) and MCP config (step 5), because those
don't inherit your shell:

```bash
export MTGA_MCP_PLAYER_LOG="<PLAYER_LOG>"
export MTGA_MCP_RAW_DIR="<RAW_DIR>"
export MTGA_MCP_UTC_LOG_DIR="<UTC_LOG_DIR>"     # omit if there is no UTC Logs dir
export MTGA_MCP_DB_PATH="$HOME/Documents/mtga-mcp/mtga.db"
```

## 3. Install deps

```bash
cd <REPO_MTGA_MCP> && uv sync
cd <REPO_COACH>    && uv sync      # pulls mtga-mcp as an editable path dep
```

## 4. Data

The DB is synced from macOS, so `cards`, `wildcards`, decks, inventory history and coach
findings are **already present** — no need to re-run `import --catalog`. Two Latitude-local
things:

```bash
cd <REPO_MTGA_MCP>
uv run mtga-mcp import --scryfall     # Scryfall CACHE is local (not synced); needed for rich queries here
uv run mtga-mcp capture               # snapshot Latitude's live inventory into the synced DB
```

(If a new set dropped and Latitude has a newer catalog, `uv run mtga-mcp import --catalog`.)

## 5. Scheduled capture (systemd --user timer)

Create `~/.config/systemd/user/mtga-capture.service`:

```ini
[Unit]
Description=MTGA inventory capture

[Service]
Type=oneshot
Environment=MTGA_MCP_DB_PATH=%h/Documents/mtga-mcp/mtga.db
Environment=MTGA_MCP_PLAYER_LOG=<PLAYER_LOG>
Environment=MTGA_MCP_RAW_DIR=<RAW_DIR>
Environment=MTGA_MCP_UTC_LOG_DIR=<UTC_LOG_DIR>
ExecStart=<REPO_MTGA_MCP>/.venv/bin/mtga-mcp capture
```

Create `~/.config/systemd/user/mtga-capture.timer`:

```ini
[Unit]
Description=Run MTGA capture every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now mtga-capture.timer
systemctl --user start mtga-capture.service   # one immediate run
journalctl --user -u mtga-capture.service -n 20 --no-pager
```

`capture` stat-checks the logs and skips when unchanged, so the 15-min cadence is near-free
when you're not playing. (On NixOS you can instead declare these as `systemd.user.services` /
`.timers` in home-manager — the imperative units above also work.)

## 6. MCP client (unified coaching + collection)

```bash
claude mcp add mtga-coach \
  --env MTGA_MCP_DB_PATH="$HOME/Documents/mtga-mcp/mtga.db" \
  --env MTGA_MCP_PLAYER_LOG="<PLAYER_LOG>" \
  --env MTGA_MCP_RAW_DIR="<RAW_DIR>" \
  --env MTGA_MCP_UTC_LOG_DIR="<UTC_LOG_DIR>" \
  -- <UV> --directory <REPO_COACH> run mtga-coach
```

## 7. Syncthing

Ensure `~/Documents` (or wherever `mtga-mcp/mtga.db` lives) is a shared Syncthing folder with
the Mac. Append the `mtga-mcp/*-journal|*-wal|*-shm` patterns from
`packaging/syncthing.stignore` to that folder's `.stignore`. Because MTGA runs on one device
at a time, the single `mtga.db` never has concurrent writers.

## 8. Verify

```bash
cd <REPO_COACH>
uv run python - <<'PY'
from mtga_replay_coach import mcp_server as m
print("recent games:", m.list_recent_games())
print("coach latest:", "markdown" in (m.coach_game() or {}))
print("saved analyses:", m.list_coach_analyses())
PY
cd <REPO_MTGA_MCP>
uv run mtga-mcp history --limit 5      # inventory snapshots (from both machines via sync)
```

Expected: your live Latitude game(s) listed; `coach_game` returns markdown and saves to the
synced DB; `history` shows snapshots.
