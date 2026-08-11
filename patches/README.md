# Patches for external tools

## `mtga-collection-exporter-macos-sigbus.patch`

Fixes a **SIGBUS crash** in [MTGA-collection-exporter](https://github.com/NthPhantom10/MTGA-collection-exporter)'s
macOS memory reader, which otherwise dies the instant it starts scanning (`zsh: bus error`).

The tool reads the running MTGA client's memory (the only way to get the *full* owned-card
list — see `src/mtga_mcp/ingest_export.py`). Its `MacOSMem.read_bytes` used `mach_vm_read` +
`ctypes.string_at`: `mach_vm_read` returns a lazily-mapped copy of the region, so touching a
page that isn't actually resident in the target faults later as a **SIGBUS** — a signal Python
can't catch, so the tool's `try/except OSError` never fires.

The patch switches to `mach_vm_read_overwrite` (a synchronous copy into our own buffer, so a
fault comes back as a KERN error code instead of a deferred signal), reading in 1 MiB chunks
and zero-filling any chunk that faults so byte offsets stay aligned with memory addresses
(the scanner reports matches as `region_addr + offset`).

Verified against the native macOS MTGA build (installed via Heroic) on Intel macOS 15.7:
exported 12,640 cards / 33,610 copies. Attach requires `sudo` (`task_for_pid`) and works
because MTGA's binary is not hardened-runtime (`codesign flags=0x0`).

### Applying

```bash
git clone https://github.com/NthPhantom10/MTGA-collection-exporter
cd MTGA-collection-exporter
git apply /path/to/this/patches/mtga-collection-exporter-macos-sigbus.patch
```

Base commit at time of writing: `1df9ee79610a90b4e688c28e329cfdebc93b980b`.
Consider upstreaming this as a PR.
