# Vendored: MTGA-collection-exporter (patched)

This is a vendored copy of [NthPhantom10/MTGA-collection-exporter](https://github.com/NthPhantom10/MTGA-collection-exporter),
MIT-licensed (see `LICENSE`, © 2026 NthPhantom10). It reads the **full** owned-card
collection out of the running MTGA client's memory — the one reliable way to get owned
counts, since the modern client no longer logs them (see `../../src/mtga_mcp/ingest_export.py`).

We vendor it so this repo is self-contained; the import bridge (`mtga-mcp import-collection`)
consumes the `mtga_collection.json` it produces.

## Local modification (already applied here)

`mtg.py` carries one patch versus upstream: the macOS memory reader (`MacOSMem.read_bytes`)
used `mach_vm_read` + `ctypes.string_at`, which returns a lazily-mapped copy that faults
later as an **uncatchable SIGBUS** on a non-resident page (`zsh: bus error` the instant
scanning starts). It's been switched to chunked `mach_vm_read_overwrite` (synchronous copy →
faults become KERN error codes), zero-filling faulting chunks to keep byte offsets aligned
with memory addresses. The standalone diff is in `../../patches/`.

Upstream base commit: `1df9ee79610a90b4e688c28e329cfdebc93b980b`. Not yet upstreamed as a PR.

## Usage

```bash
# from this directory, with MTGA running and the Collection screen opened once:
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
sudo ./.venv/bin/python mtg.py          # macOS: sudo needed for task_for_pid
# then, from the repo root:
uv run mtga-mcp import-collection third_party/mtga-collection-exporter/mtga_collection.json
```

macOS notes: `task_for_pid` requires `sudo`, and works because the native MTGA build
(installed via Heroic) is not hardened-runtime. It first prompts for a couple of rare/
legendary "anchor" cards you own (with exact quantities) to locate the collection in memory;
anchors are cached in `last_anchors.json` for reuse.
