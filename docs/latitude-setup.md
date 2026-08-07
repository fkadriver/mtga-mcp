# Latitude (NixOS + Heroic) setup

Status: **done** (2026-08-07). This documents how **mtga-mcp** + **mtga-replay-coach** are
actually wired up on the Linux/NixOS laptop ("Latitude"), where MTGA runs via Heroic
(Wine/Proton). macOS (Airbook) is configured separately; this only covers the Linux side.

Repos: `~/git/mtga-mcp`, `~/git/mtga-replay-coach`. NixOS config: `~/git/nixos`.

## 1. Resolved paths

```
PLAYER_LOG = /home/scott/Games/Heroic/Prefixes/default/Magic The Gathering Arena/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log
RAW_DIR    = /home/scott/Games/Heroic/MagicTheGathering/MTGA_Data/Downloads/Raw
UTC_LOG_DIR = (none — no rotating UTC_Log*.log dir under this Heroic prefix; that's fine)
DB_PATH    = /home/scott/Documents/mtga-mcp/mtga.db   (synced from macOS via Syncthing)
```

Re-discover these if the Heroic prefix ever changes:

```bash
find ~ -name 'Player.log' -path '*Wizards*MTGA*' 2>/dev/null
find ~ -iname 'Raw_CardDatabase_*.mtga' 2>/dev/null | sed 's#/[^/]*$##' | sort -u
```

## 2. Environment variables — declared via home-manager, not `~/.profile`

Latitude is NixOS, so `MTGA_MCP_PLAYER_LOG` / `MTGA_MCP_RAW_DIR` / `MTGA_MCP_DB_PATH` are
declared as `home.sessionVariables` in `~/git/nixos/hosts/latitude/default.nix`, inside a
`home-manager.users.scott = { ... }` block — **not** in the shared
`homeConfigurations/scott.nix` base module, since that file is also used by Airbook and these
paths are latitude-specific (Wine prefix location).

Applied with:

```bash
cd ~/git/nixos && sudo nixos-rebuild switch --flake .#latitude
```

**Gotcha:** home-manager's session-vars script (`hm-session-vars.sh`, sourced from
`~/.profile`) guards itself with `__HM_SESS_VARS_SOURCED=1` and skips re-running if that's
already set — which it will be in any shell that was open before the rebuild (its child
processes inherit the guard var too). New vars only show up in **genuinely new** shells
(new terminal, new SSH login) opened after the switch, not by re-sourcing `.profile` in an
existing session.

## 3. Deps

```bash
cd ~/git/mtga-mcp        && uv sync
cd ~/git/mtga-replay-coach && uv sync      # pulls mtga-mcp as an editable path dep
```

## 4. Data

The DB synced from macOS already had `cards`, `wildcards`, decks, inventory history and coach
findings, so `import --catalog` wasn't needed. Ran the two Latitude-local steps:

```bash
cd ~/git/mtga-mcp
uv run mtga-mcp import --scryfall     # 18,721 cards enriched (cache is local, not synced)
uv run mtga-mcp capture               # snapshot Latitude's live inventory into the synced DB
```

## 5. Scheduled capture — home-manager `systemd.user`, not raw unit files

Declared in the same `hosts/latitude/default.nix` block as step 2, as
`systemd.user.services.mtga-capture` (oneshot) + `systemd.user.timers.mtga-capture`
(`OnBootSec=2min`, `OnUnitActiveSec=15min`, `Persistent=true`), `ExecStart` pointing at
`~/git/mtga-mcp/.venv/bin/mtga-mcp capture`.

**Gotcha:** `Player.log`'s path contains spaces (`Magic The Gathering Arena`). systemd's
`Environment=` word-splits unquoted assignments on whitespace — an unquoted
`Environment=MTGA_MCP_PLAYER_LOG=/path/with spaces/Player.log` silently breaks into multiple
"Invalid environment assignment" errors and the var never gets set. Fix: quote the **whole**
`VAR=value` assignment, not just the value:

```nix
Environment = [
  "MTGA_MCP_DB_PATH=%h/Documents/mtga-mcp/mtga.db"
  ''"MTGA_MCP_PLAYER_LOG=/home/scott/Games/.../Player.log"''  # note the embedded quotes
  "MTGA_MCP_RAW_DIR=/home/scott/Games/.../Raw"
];
```

Verify after `nixos-rebuild switch`:

```bash
systemctl --user daemon-reload
systemctl --user start mtga-capture.service
journalctl --user -u mtga-capture.service -n 20 --no-pager   # should show "+N new InventoryInfo payloads"
systemctl --user status mtga-capture.timer --no-pager        # Active: active (waiting)
```

## 6. MCP client (unified coaching + collection)

```bash
claude mcp add mtga-coach \
  --env MTGA_MCP_DB_PATH="$HOME/Documents/mtga-mcp/mtga.db" \
  --env MTGA_MCP_PLAYER_LOG="/home/scott/Games/Heroic/Prefixes/default/Magic The Gathering Arena/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log" \
  --env MTGA_MCP_RAW_DIR="/home/scott/Games/Heroic/MagicTheGathering/MTGA_Data/Downloads/Raw" \
  -- /run/current-system/sw/bin/uv --directory ~/git/mtga-replay-coach run mtga-coach
```

**Note:** `claude mcp add` without `-s` registers at **local** scope — tied to the project
directory you run it from (here, `~/git/mtga-mcp`). It won't be available to `claude`
sessions started elsewhere. Re-run with `-s user` if you want it global.

## 7. Syncthing

`~/Documents` (containing `mtga-mcp/mtga.db`) is already a Syncthing folder shared with
`airbook-darwin` and `nas01`, declared in `~/git/nixos/hosts/latitude/syncthing.nix`
(`services.syncthing-declarative`) — no changes needed there.

There was no `~/Documents/.stignore` yet, so created one with the patterns from
`packaging/syncthing.stignore`:

```
mtga-mcp/*-journal
mtga-mcp/*-wal
mtga-mcp/*-shm
```

**`.stignore` is per-device and is NOT synced.** Syncthing reads it locally and deliberately
excludes it from transfer, so each device that shares the folder needs its own copy — the
Latitude one will never reach Airbook. Create the same file on every writing device
(`~/Documents/.stignore` with the three patterns above); Syncthing auto-reloads it on change,
no restart needed. Status: created on Latitude and on Airbook (2026-08-07). `nas01` doesn't
run MTGA so it never writes sidecars — a `.stignore` there is optional.

## 8. Verify

```bash
cd ~/git/mtga-replay-coach
uv run python - <<'PY'
from mtga_replay_coach import mcp_server as m
print("recent games:", m.list_recent_games())
print("coach latest:", "markdown" in (m.coach_game() or {}))
print("saved analyses:", m.list_coach_analyses())
PY
cd ~/git/mtga-mcp
uv run mtga-mcp history --limit 5      # inventory snapshots (from both machines via sync)
```

Confirmed: 2 recent games listed, `coach_game` returned markdown and saved an analysis,
`history` showed snapshots spanning both machines.
