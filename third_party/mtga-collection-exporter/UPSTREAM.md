# Vendored upstream reference: MTGA-collection-exporter

- **Upstream:** https://github.com/NthPhantom10/MTGA-collection-exporter
- **License:** MIT (see `../../LICENSES/mtga-collection-exporter-MIT.txt`)
- **Pinned baseline commit:** `e1424956f1a8f92c7fa28c607a0fcc3fb625ef62`

## What this is

`mtg.py` here is a **pristine, unmodified** copy of upstream at the pinned commit. It is
**reference-only** — it is *not* part of the `mtga_mcp` package, is not imported, and is not
executed. The maintained, in-tree version of the scanner is
[`src/mtga_mcp/memory_export.py`](../../src/mtga_mcp/memory_export.py), which is *derived* from
this file (macOS SIGBUS fix, catalog-driven card lookup, `pgrep` process discovery, DB-seeded
anchors, raw `{grp_id: qty}` output, and a chunked pattern scan shared by the macOS/Linux
readers that covers regions of any size instead of skipping ones over 256 MiB).

## Why keep it

So we can pull improvements from upstream. The pinned copy is the baseline our port
corresponds to, so diffing a newer upstream `mtg.py` against it shows exactly what changed and
therefore what might be worth porting into `memory_export.py`.

## How to pull upstream changes

```bash
scripts/update-exporter-reference.sh            # show what changed upstream since the baseline
# ...port the relevant hunks into src/mtga_mcp/memory_export.py by hand, then:
scripts/update-exporter-reference.sh --accept   # advance the baseline to upstream HEAD
```

The pinned commit above is updated by `--accept`; keep it in sync with what `memory_export.py`
currently reflects.
