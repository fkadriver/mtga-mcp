#!/usr/bin/env bash
#
# Scan the running MTGA client's memory for your full collection and import it into the
# mtga-mcp database, using the built-in `mtga-mcp export-collection` command.
#
# This runs attached to your terminal because it's interactive:
#   - sudo prompts for your password (the macOS memory scan needs task_for_pid / root);
#   - it proposes ~5 anchor cards pulled from your last-imported collection and lets you
#     confirm or adjust them (quantities must match what you own right now). On a first-ever
#     run with an empty collection you enter a few cards manually.
# Do not redirect stdin; the prompts need the keyboard.
#
# Prereqs: MTGA RUNNING with the Collection screen opened once; `uv` on PATH; the card catalog
# already imported (`mtga-mcp import --catalog`).
#
# Usage:  scripts/export-collection.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python"

command -v uv >/dev/null 2>&1 || { echo "error: 'uv' not found on PATH" >&2; exit 1; }

# Ensure the project venv exists (uv sync builds it from pyproject/uv.lock).
if [[ ! -x "$VENV_PY" ]]; then
    echo "==> Creating project venv…"
    ( cd "$REPO_ROOT" && uv sync )
fi

# Resolve the invoking user's data dir/DB now, while we're still that user, and pass them
# through sudo -- otherwise the scan runs as root and paths.py would resolve ~root/.local.
DATA_DIR="${MTGA_MCP_DATA_DIR:-$HOME/.local/share/mtga-mcp}"
DB_PATH="${MTGA_MCP_DB_PATH:-$DATA_DIR/mtga.db}"

echo "==> Scanning MTGA memory (sudo required)…"
sudo env \
    "MTGA_MCP_DATA_DIR=$DATA_DIR" \
    "MTGA_MCP_DB_PATH=$DB_PATH" \
    "$VENV_PY" -m mtga_mcp.cli export-collection "$@"

echo "==> Done."
