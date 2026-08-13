#!/usr/bin/env bash
#
# One-shot: dump the full owned collection from the running MTGA client's memory
# and import it into the mtga-mcp database.
#
#   1. ensures the vendored exporter's venv exists (creates it on first run)
#   2. runs third_party/mtga-collection-exporter/mtg.py under sudo (needs task_for_pid)
#   3. imports the resulting mtga_collection.json via `uv run mtga-mcp import-collection`
#
# Prereqs: MTGA must be RUNNING with the Collection screen opened at least once, and
# `uv` must be on PATH. You'll be prompted for your sudo password.
#
# Usage:  scripts/export-collection.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPORTER="$REPO_ROOT/third_party/mtga-collection-exporter"
VENV_PY="$EXPORTER/.venv/bin/python"
EXPORT_JSON="$EXPORTER/mtga_collection.json"

command -v uv >/dev/null 2>&1 || { echo "error: 'uv' not found on PATH" >&2; exit 1; }

# 1. First-run venv bootstrap for the exporter.
if [[ ! -x "$VENV_PY" ]]; then
    echo "==> Creating exporter venv (first run)…"
    uv venv "$EXPORTER/.venv"
    uv pip install --python "$VENV_PY" -r "$EXPORTER/requirements.txt"
fi

# 2. Run the memory scanner. It needs root (task_for_pid) and always writes its output
#    next to mtg.py. Feed a newline to satisfy the trailing "Press Enter to exit" prompt;
#    sudo still reads the password from the terminal, not this pipe.
echo "==> Scanning MTGA memory (sudo required)…"
printf '\n' | sudo "$VENV_PY" "$EXPORTER/mtg.py" --no-explorer

# The scanner runs as root, so its output files are root-owned. Hand them back so
# future non-sudo runs (and the import below) aren't blocked.
sudo chown "$(id -un):$(id -gn)" \
    "$EXPORT_JSON" \
    "$EXPORTER"/mtga_collection* \
    "$EXPORTER"/arena_id_lookup* \
    "$EXPORTER"/last_anchors.json \
    "$EXPORTER"/mtga_exporter.log 2>/dev/null || true

[[ -s "$EXPORT_JSON" ]] || { echo "error: export produced no $EXPORT_JSON" >&2; exit 1; }

# 3. Import into the mtga-mcp database.
echo "==> Importing $EXPORT_JSON …"
cd "$REPO_ROOT"
uv run mtga-mcp import-collection "$EXPORT_JSON"

echo "==> Done."
