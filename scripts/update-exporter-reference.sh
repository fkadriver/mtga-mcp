#!/usr/bin/env bash
#
# Track upstream MTGA-collection-exporter so we can port fixes into our derived scanner
# (src/mtga_mcp/memory_export.py).
#
#   (no args)   Fetch upstream mtg.py at HEAD and diff it against our pinned baseline copy,
#               showing what changed upstream since we last synced. Port relevant hunks into
#               src/mtga_mcp/memory_export.py by hand.
#   --accept    After porting, advance the baseline: overwrite the vendored mtg.py with upstream
#               HEAD and update the pinned commit in UPSTREAM.md.
#
# Needs: curl, python3.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/third_party/mtga-collection-exporter"
VENDORED="$VENDOR_DIR/mtg.py"
UPSTREAM_MD="$VENDOR_DIR/UPSTREAM.md"

API="https://api.github.com/repos/NthPhantom10/MTGA-collection-exporter/commits/main"
RAW="https://raw.githubusercontent.com/NthPhantom10/MTGA-collection-exporter"

command -v curl >/dev/null 2>&1 || { echo "error: curl not found" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "error: python3 not found" >&2; exit 1; }

echo "==> Resolving upstream HEAD…"
HEAD_SHA="$(curl -s "$API" | python3 -c 'import sys,json; print(json.load(sys.stdin)["sha"])')"
[[ -n "$HEAD_SHA" ]] || { echo "error: could not resolve upstream HEAD" >&2; exit 1; }
echo "    upstream HEAD: $HEAD_SHA"

PINNED="$(grep -oE '[0-9a-f]{40}' "$UPSTREAM_MD" | head -1 || true)"
echo "    our baseline:  ${PINNED:-<none>}"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -s -o "$TMP" "$RAW/$HEAD_SHA/mtg.py"
[[ -s "$TMP" ]] || { echo "error: failed to download upstream mtg.py" >&2; exit 1; }

if [[ "${1:-}" == "--accept" ]]; then
    cp "$TMP" "$VENDORED"
    # Replace the pinned SHA in UPSTREAM.md with HEAD.
    python3 - "$UPSTREAM_MD" "$HEAD_SHA" <<'PY'
import re, sys
path, sha = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(r'`[0-9a-f]{40}`', f'`{sha}`', text, count=1)
open(path, "w", encoding="utf-8").write(text)
PY
    echo "==> Baseline advanced to $HEAD_SHA. Review 'git diff' and commit."
    exit 0
fi

if [[ "$PINNED" == "$HEAD_SHA" ]]; then
    echo "==> Already at upstream HEAD — nothing new to port."
    exit 0
fi

echo
echo "==> Upstream changes to mtg.py since our baseline (port relevant hunks into"
echo "    src/mtga_mcp/memory_export.py, then re-run with --accept):"
echo
git --no-pager diff --no-index -- "$VENDORED" "$TMP" || true
