# Vendored upstream reference: MTGA-collection-exporter

- **Upstream:** https://github.com/NthPhantom10/MTGA-collection-exporter
- **License:** MIT (see `../../LICENSES/mtga-collection-exporter-MIT.txt`)
- **Pinned baseline commit:** `1df9ee79610a90b4e688c28e329cfdebc93b980b`

## What this is

`mtg.py` here is a **pristine, unmodified** copy of upstream at the pinned commit. It is
**reference-only** — it is *not* part of the `mtga_mcp` package, is not imported, and is not
executed. The maintained, in-tree version of the scanner is
[`src/mtga_mcp/memory_export.py`](../../src/mtga_mcp/memory_export.py), which is *derived* from
this file (macOS SIGBUS fix, catalog-driven card lookup, `pgrep` process discovery, DB-seeded
anchors, raw `{grp_id: qty}` output).

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
