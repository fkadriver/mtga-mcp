"""Read the full owned collection out of the running MTGA client's process memory.

Derived from MTGA-collection-exporter (https://github.com/NthPhantom10/MTGA-collection-exporter),
MIT-licensed by NthPhantom10 -- see LICENSES/mtga-collection-exporter-MIT.txt. A pristine copy of
the upstream source we ported from is kept at third_party/mtga-collection-exporter/mtg.py (see
its UPSTREAM.md); run scripts/update-exporter-reference.sh to diff newer upstream against it and
pull fixes back into this file. Adapted for mtga-mcp: this module knows nothing about card names
or Scryfall. It takes a set of known
arena_ids (our `cards` catalog) purely to *score* candidate memory blocks, plus a few "anchor"
cards (arena_id + the exact owned quantity) whose 32-bit `(id, qty)` pair it searches for to
locate the collection. It returns the raw ``{arena_id(grp_id): quantity}`` block, which the
caller writes straight into `collection` -- no name aggregation, no distribute heuristic.

The collection lives in memory as a run of little-endian ``uint32`` pairs (arena_id, quantity).
We find an anchor's exact byte pattern, read a window of memory around each hit, and walk it at
several word strides/offsets pulling out the longest plausible ``(id, qty)`` run, then score the
candidates by how many ids we recognise and how many anchors they contain.

macOS reads memory via the Mach VM API; ``task_for_pid`` requires root (run under sudo). The
read path uses ``mach_vm_read_overwrite`` (a synchronous copy into our buffer) rather than
``mach_vm_read``: a fault comes back as a KERN error here instead of being deferred to a SIGBUS
when we later touch a not-actually-resident page in a lazily-mapped out-of-line copy. Windows
support uses ``pymem`` (imported lazily; not a hard dependency).
"""

from __future__ import annotations

import ctypes
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set, Tuple

if TYPE_CHECKING:
    from .anchors import Anchor


@dataclass
class ScanConfig:
    """Tunable knobs for the memory scan (defaults match the upstream exporter)."""
    scan_range_mb: int = 8       # window read around each anchor hit
    min_arena_id: int = 1000     # plausible arena_id range for a collection entry
    max_arena_id: int = 900_000
    min_qty: int = 1             # plausible owned-copy range
    max_qty: int = 400
    min_block_size: int = 50     # a real collection run is long
    max_gap: int = 64            # words of non-pairs tolerated before a run ends


def _find_mtga_pid(process_name: str = "MTGA") -> int:
    """Return the pid of the running MTGA client via `pgrep`, or raise."""
    try:
        out = subprocess.run(
            ["pgrep", "-x", process_name],
            capture_output=True, text=True, check=False,
        ).stdout.split()
    except FileNotFoundError as exc:  # no pgrep
        raise RuntimeError("`pgrep` not available to locate the MTGA process") from exc
    if not out:
        raise RuntimeError(f"Process not found: {process_name} (is MTGA running?)")
    return int(out[0])


if sys.platform == "darwin":

    class MacOSMem:
        """macOS process-memory reader using the Mach VM API."""

        _KERN_SUCCESS = 0
        _VM_REGION_BASIC_INFO_64 = 9
        _VM_REGION_BASIC_INFO_COUNT_64 = 9
        _VM_PROT_READ = 0x01
        _CHUNK = 1 << 20  # 1 MiB per mach_vm_read_overwrite call

        class _RegionInfo(ctypes.Structure):
            _pack_ = 4
            _fields_ = [
                ("protection", ctypes.c_int32),
                ("max_protection", ctypes.c_int32),
                ("inheritance", ctypes.c_uint32),
                ("shared", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32),
                ("offset", ctypes.c_uint64),
                ("behavior", ctypes.c_int32),
                ("user_wired_count", ctypes.c_uint16),
                ("_pad", ctypes.c_uint16),
            ]

        def __init__(self, process_name: str = "MTGA"):
            self._lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            self._setup_funcs()

            self.process_id = _find_mtga_pid(process_name)

            self_task = ctypes.c_uint.in_dll(self._lib, "mach_task_self_").value
            self._task = ctypes.c_uint(0)
            kr = self._lib.task_for_pid(self_task, self.process_id, ctypes.byref(self._task))
            if kr != self._KERN_SUCCESS:
                raise PermissionError(
                    f"task_for_pid failed (err={kr}). Run with sudo, and ensure the MTGA "
                    "process is not hardened-runtime (the Heroic/native build is fine)."
                )

        def _setup_funcs(self) -> None:
            lib = self._lib
            lib.task_for_pid.restype = ctypes.c_int
            lib.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
            lib.mach_vm_read_overwrite.restype = ctypes.c_int
            lib.mach_vm_read_overwrite.argtypes = [
                ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint64,
                ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64),
            ]
            lib.mach_vm_region.restype = ctypes.c_int
            lib.mach_vm_region.argtypes = [
                ctypes.c_uint, ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
                ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
            ]

        def read_bytes(self, address: int, length: int) -> bytes:
            # Chunked, synchronous reads; zero-fill any chunk that faults so byte offsets in the
            # returned buffer stay aligned with memory addresses (matches pattern offsets).
            if address < 0 or length <= 0:
                return b""
            buf = ctypes.create_string_buffer(min(length, self._CHUNK))
            buf_addr = ctypes.cast(buf, ctypes.c_void_p).value
            outsize = ctypes.c_uint64(0)
            out = bytearray()
            cur = address
            remaining = length
            while remaining > 0:
                n = min(remaining, self._CHUNK)
                kr = self._lib.mach_vm_read_overwrite(
                    self._task.value, cur, n, buf_addr, ctypes.byref(outsize),
                )
                if kr != self._KERN_SUCCESS:
                    out += b"\x00" * n
                else:
                    got = outsize.value or n
                    out += buf.raw[:got]
                    if got < n:
                        out += b"\x00" * (n - got)
                cur += n
                remaining -= n
            return bytes(out)

        def _readable_regions(self):
            addr = ctypes.c_uint64(0)
            while True:
                size = ctypes.c_uint64(0)
                info = self._RegionInfo()
                cnt = ctypes.c_uint(self._VM_REGION_BASIC_INFO_COUNT_64)
                obj = ctypes.c_uint(0)
                kr = self._lib.mach_vm_region(
                    self._task.value, ctypes.byref(addr), ctypes.byref(size),
                    self._VM_REGION_BASIC_INFO_64, ctypes.byref(info),
                    ctypes.byref(cnt), ctypes.byref(obj),
                )
                if kr != self._KERN_SUCCESS:
                    break
                if info.protection & self._VM_PROT_READ:
                    yield addr.value, size.value
                addr.value += size.value

        def pattern_scan_all(self, pattern: bytes, return_multiple: bool = False) -> List[int]:
            results: List[int] = []
            for addr, size in self._readable_regions():
                if size > 256 * 1024 * 1024:
                    continue
                try:
                    data = self.read_bytes(addr, size)
                except OSError:
                    continue
                offset = 0
                while True:
                    idx = data.find(pattern, offset)
                    if idx == -1:
                        break
                    results.append(addr + idx)
                    if not return_multiple:
                        return results
                    offset = idx + 1
            return results


class MemoryScanner:
    """Locate the collection block in MTGA's memory given anchor cards.

    The block-extraction and scoring methods (`_extract`, `_select_best`, `_validate`) are pure
    functions of bytes/dicts and are unit-tested without a live process.
    """

    STRIDES_WORDS = (2, 3, 4)

    def __init__(self, cfg: ScanConfig, known_ids: Set[int]):
        self.cfg = cfg
        self.known_ids = known_ids
        self.pm: Optional[Any] = None

    def connect(self) -> bool:
        try:
            if sys.platform == "darwin":
                self.pm = MacOSMem("MTGA")
            elif sys.platform == "win32":
                import pymem  # optional, Windows-only

                self.pm = pymem.Pymem("MTGA.exe")
            else:
                raise RuntimeError("Memory scanning is supported only on macOS/Windows.")
            return True
        except Exception as exc:
            print(f"\n✗ Cannot attach to MTGA: {exc}")
            print("  Launch MTGA, open the Collection screen so it loads into memory, then retry.")
            return False

    def find_collection(self, anchors: Sequence["Anchor"]) -> Optional[Dict[int, int]]:
        if not self.pm or not anchors:
            return None

        ordered = sorted(anchors, key=lambda a: -a.quantity)
        for idx, primary in enumerate(ordered, 1):
            print(f"  [{idx}/{len(ordered)}] anchor {primary.name} "
                  f"(id {primary.arena_id} x{primary.quantity}) …", flush=True)

            addrs = self._scan_anchor(primary)
            if not addrs:
                continue
            candidates = self._find_blocks(addrs)
            if not candidates:
                continue

            best = self._select_best(candidates, anchors)
            if best:
                block, duplicates = best
                if self._validate(block, duplicates):
                    # Trust the user-confirmed anchor quantities over whatever the block held.
                    for a in anchors:
                        if a.arena_id in block:
                            block[a.arena_id] = a.quantity
                    known = sum(1 for k in block if k in self.known_ids)
                    print(f"  ✓ collection block found: {len(block)} entries "
                          f"({known} known to catalog)")
                    return block

        print("  ✗ all anchors exhausted without a valid collection block.")
        return None

    def _scan_anchor(self, anchor: "Anchor") -> List[int]:
        pattern = struct.pack("<II", anchor.arena_id, anchor.quantity)
        if sys.platform != "darwin":
            pattern = re.escape(pattern)
        try:
            return self.pm.pattern_scan_all(pattern, return_multiple=True)
        except Exception as exc:
            print(f"    scan error: {exc}")
            return []

    def _find_blocks(self, addresses: List[int]) -> List[Tuple[Dict[int, int], int]]:
        scan_bytes = self.cfg.scan_range_mb * 1024 * 1024
        addresses = sorted(set(addresses))

        filtered: List[int] = []
        for a in addresses:
            if not filtered or a - filtered[-1] > 1024 * 1024:
                filtered.append(a)

        blocks: List[Tuple[Dict[int, int], int]] = []
        for addr in filtered:
            blocks.extend(self._scan_region(addr, scan_bytes))
        return blocks

    def _scan_region(self, addr: int, size: int) -> List[Tuple[Dict[int, int], int]]:
        start = max(0, addr - size // 2)
        try:
            data = self.pm.read_bytes(start, size)
        except Exception:
            return []
        results: List[Tuple[Dict[int, int], int]] = []
        for stride_w in self.STRIDES_WORDS:
            for off_w in range(stride_w):
                results.extend(self._extract(data, stride_w, off_w))
        return results

    def _extract(self, data: bytes, stride_w: int, off_w: int) -> List[Tuple[Dict[int, int], int]]:
        n_ints = len(data) // 4
        if n_ints < 2:
            return []
        try:
            ints = struct.unpack_from(f"<{n_ints}I", data)
        except struct.error:
            return []

        blocks: List[Tuple[Dict[int, int], int]] = []
        current: Dict[int, int] = {}
        duplicates = 0
        misses = 0
        i = off_w
        while i + 1 < n_ints:
            k, v = ints[i], ints[i + 1]
            if (self.cfg.min_arena_id <= k < self.cfg.max_arena_id
                    and self.cfg.min_qty <= v <= self.cfg.max_qty):
                if k in current:
                    duplicates += 1
                else:
                    current[k] = v
                misses = 0
            else:
                misses += 1
                if misses > self.cfg.max_gap:
                    if len(current) >= self.cfg.min_block_size:
                        blocks.append((current, duplicates))
                    current = {}
                    duplicates = 0
                    misses = 0
            i += stride_w

        if len(current) >= self.cfg.min_block_size:
            blocks.append((current, duplicates))
        return blocks

    def _select_best(
        self, candidates: List[Tuple[Dict[int, int], int]], anchors: Sequence["Anchor"],
    ) -> Optional[Tuple[Dict[int, int], int]]:
        if not candidates:
            return None

        anchor_pairs = {(a.arena_id, a.quantity) for a in anchors}
        anchor_ids = {a.arena_id for a in anchors}
        n_anchors = max(1, len(anchors))

        scored: List[Tuple[Dict[int, int], float, int, int, int]] = []
        for blk, dupes in candidates:
            if not blk:
                continue
            known = sum(1 for k in blk if k in self.known_ids)
            known_ratio = known / len(blk)
            anchors_exact = sum(1 for k, v in blk.items() if (k, v) in anchor_pairs)
            anchors_id = sum(1 for k in blk if k in anchor_ids)
            size_score = min(len(blk) / 5000, 1.0)
            dup_ratio = dupes / max(1, len(blk) + dupes)
            score = (
                known_ratio * 0.35
                + (anchors_exact / n_anchors) * 0.35
                + (anchors_id / n_anchors) * 0.10
                + size_score * 0.10
                + (1.0 - dup_ratio) * 0.10
            )
            scored.append((blk, score, anchors_exact, known, dupes))

        # Prefer duplicate-free blocks, then score, exact-anchor count, known count, size.
        scored.sort(key=lambda x: (x[4] == 0, x[1], x[2], x[3], len(x[0])), reverse=True)
        best = scored[0]
        return best[0], best[4]

    def _validate(self, block: Dict[int, int], duplicates: int = 0) -> bool:
        if not block or len(block) < 10 or len(block) > 100_000:
            return False
        if sum(1 for k in block if k in self.known_ids) / len(block) < 0.30:
            return False
        if sum(block.values()) > 500_000:
            return False
        if duplicates > max(25, int(len(block) * 0.05)):
            return False
        return True


def scan(
    known_ids: Set[int], anchors: Sequence["Anchor"], cfg: Optional[ScanConfig] = None,
) -> Optional[Dict[int, int]]:
    """Attach to MTGA and return the ``{grp_id: quantity}`` collection block, or None."""
    scanner = MemoryScanner(cfg or ScanConfig(), known_ids)
    if not scanner.connect():
        return None
    return scanner.find_collection(anchors)
