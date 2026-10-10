"""Tests for the pure block-extraction/scoring logic of the memory scanner.

These exercise the risky parsing without a live MTGA process: `_extract` walks a synthetic
little-endian (id, qty) buffer, and `_select_best`/`_validate` score dict blocks.
"""

from __future__ import annotations

import struct

from mtga_mcp.anchors import Anchor
from mtga_mcp.memory_export import (
    MemoryScanner,
    ScanConfig,
    _ChunkedPatternScan,
    _parse_proc_maps,
    _parse_proc_smaps,
    _region_has_pages,
)


def _buf(pairs: list[tuple[int, int]]) -> bytes:
    ints: list[int] = []
    for k, v in pairs:
        ints += [k, v]
    return struct.pack(f"<{len(ints)}I", *ints)


def _scanner(known=range(1000, 1100)) -> MemoryScanner:
    return MemoryScanner(ScanConfig(), set(known))


def test_extract_pulls_contiguous_block():
    sc = _scanner()
    pairs = [(1000 + i, (i % 4) + 1) for i in range(60)]  # 60 >= min_block_size
    blocks = sc._extract(_buf(pairs), stride_w=2, off_w=0)
    assert len(blocks) == 1
    blk, dupes = blocks[0]
    assert len(blk) == 60 and dupes == 0
    assert blk[1000] == 1 and blk[1059] == (59 % 4) + 1


def test_extract_ignores_short_runs():
    sc = _scanner()
    # Only 10 pairs -> below min_block_size (50), so nothing is emitted.
    blocks = sc._extract(_buf([(1000 + i, 2) for i in range(10)]), 2, 0)
    assert blocks == []


def test_extract_counts_duplicate_ids():
    sc = _scanner()
    pairs = [(1000 + i, 1) for i in range(55)]
    pairs[54] = (1000, 1)  # repeat an id already seen in the run
    blocks = sc._extract(_buf(pairs), 2, 0)
    assert len(blocks) == 1
    blk, dupes = blocks[0]
    assert dupes == 1 and 1000 in blk


def test_select_best_prefers_block_with_exact_anchor():
    sc = _scanner()
    anchors = [Anchor(1005, 3, "Anchor")]
    base = {1000 + i: 2 for i in range(60)}
    with_anchor = dict(base)
    with_anchor[1005] = 3  # matches the anchor's exact (id, qty)
    best = sc._select_best([(base, 0), (with_anchor, 0)], anchors)
    assert best[0] is with_anchor


def test_validate_rejects_low_known_ratio():
    sc = _scanner(known=range(1000, 1005))  # only 5 known ids
    block = {1000 + i: 1 for i in range(60)}  # 5/60 known < 0.30
    assert sc._validate(block, 0) is False


def test_validate_accepts_healthy_block():
    sc = _scanner()
    block = {1000 + i: 2 for i in range(60)}
    assert sc._validate(block, 0) is True


# --- /proc/<pid>/maps parsing and chunked pattern scanning ------------------------------------
# Both are shared by the macOS and Linux readers, and are pure functions of text/bytes, so they
# run on any platform (the readers themselves are defined only on their own platform).

MAPS = """\
55a3b2c00000-55a3b2c21000 r--p 00000000 fd:00 1234    /games/MTGA.exe
55a3b2c21000-55a3b2c22000 ---p 00021000 fd:00 1234    /games/MTGA.exe
7f1200000000-7f1200010000 rw-p 00000000 00:00 0
7ffd1b3f0000-7ffd1b3f2000 r--p 00000000 00:00 0       [vvar]
ffffffffff600000-ffffffffff601000 r-xp 00000000 00:00 0 [vsyscall]
7f1300000000-7f1300001000 rw-s 00000000 00:0e 42      /dev/nvidia0
"""


def test_parse_proc_maps_keeps_readable_regions_only():
    regions = list(_parse_proc_maps(MAPS))
    # The non-readable (---p), [vvar], [vsyscall] and /dev/ lines are all dropped.
    assert regions == [
        (0x55A3B2C00000, 0x21000),
        (0x7F1200000000, 0x10000),
    ]


def test_parse_proc_maps_tolerates_garbage_lines():
    assert list(_parse_proc_maps("not a maps line\n\nzzz-yyy r--p 0 0 0\n")) == []


class _FakeMem(_ChunkedPatternScan):
    """Chunked scanner over an in-memory 'region', counting reads to prove it chunks."""

    _SCAN_CHUNK = 64

    def __init__(self, data: bytes, base: int = 0x1000):
        self.data, self.base, self.reads = data, base, 0

    def _readable_regions(self):
        yield self.base, len(self.data)

    def read_bytes(self, address: int, length: int) -> bytes:
        self.reads += 1
        start = address - self.base
        chunk = self.data[start:start + length]
        return chunk + b"\x00" * (length - len(chunk))  # same zero-fill contract as the readers


def test_chunked_scan_finds_pattern_straddling_a_chunk_boundary():
    pattern = struct.pack("<II", 70_000, 4)
    # Straddle the 64-byte chunk boundary: 6 bytes before it, 2 after.
    data = bytearray(b"\xaa" * 200)
    data[58:66] = pattern
    mem = _FakeMem(bytes(data))
    assert mem.pattern_scan_all(pattern, return_multiple=True) == [0x1000 + 58]
    assert mem.reads > 1  # really chunked, not one big read


def test_chunked_scan_returns_all_hits_and_stops_early_when_asked():
    pattern = struct.pack("<II", 1234, 2)
    data = bytearray(b"\x00" * 300)
    for at in (10, 120, 250):
        data[at:at + 8] = pattern
    mem = _FakeMem(bytes(data))
    assert mem.pattern_scan_all(pattern, return_multiple=True) == [0x1000 + a for a in (10, 120, 250)]
    assert mem.pattern_scan_all(pattern) == [0x1000 + 10]


def test_chunked_scan_does_not_double_report_overlap():
    # A hit inside the re-read overlap window must not be reported twice.
    pattern = struct.pack("<II", 999, 1)
    data = bytearray(b"\x11" * 128)
    data[60:68] = pattern  # lands in the overlap that the next slice re-reads
    mem = _FakeMem(bytes(data))
    assert mem.pattern_scan_all(pattern, return_multiple=True) == [0x1000 + 60]


# --- residency filtering ----------------------------------------------------------------------
# A region with neither resident nor swapped-out pages is untouched reserved space: it cannot
# hold the collection, and reading it faults zero pages into the game. Swapped pages are real
# data that merely got paged out, so those regions must still be scanned.

SMAPS = """\
55a3b2c00000-55a3b2c21000 r--p 00000000 fd:00 1234    /games/MTGA.exe
Size:                132 kB
Rss:                 100 kB
Swap:                  0 kB
7ff880000000-7ff940000000 rw-p 00000000 00:00 0
Size:            3145728 kB
Rss:                   0 kB
Swap:                  0 kB
600000000000-600020000000 rw-p 00000000 00:00 0
Size:             524288 kB
Rss:                   0 kB
Swap:               3124 kB
7ff000000000-7ff000001000 ---p 00000000 00:00 0
Size:                  4 kB
Rss:                  48 kB
Swap:                  0 kB
"""


def test_region_has_pages_only_rejects_wholly_untouched_regions():
    assert _region_has_pages(0, 0) is False
    assert _region_has_pages(1, 0) is True
    assert _region_has_pages(0, 1) is True  # paged out, but real data


def test_parse_proc_smaps_skips_reservations_but_keeps_swapped_regions():
    assert list(_parse_proc_smaps(SMAPS)) == [
        (0x55A3B2C00000, 0x21000),      # live file-backed mapping
        (0x600000000000, 0x20000000),   # resident 0 but 3124 kB swapped -> still scanned
    ]
    # The 3 GiB untouched reservation and the non-readable (---p) mapping are both gone.


def test_parse_proc_smaps_handles_final_region_and_empty_input():
    tail = "7f1200000000-7f1200010000 rw-p 00000000 00:00 0\nRss:  8 kB\nSwap: 0 kB\n"
    assert list(_parse_proc_smaps(tail)) == [(0x7F1200000000, 0x10000)]
    assert list(_parse_proc_smaps("")) == []


def test_parse_proc_smaps_without_counters_is_dropped_not_crashed():
    # Defensive: a header with no Rss/Swap lines reads as untouched rather than raising.
    assert list(_parse_proc_smaps("7f1200000000-7f1200010000 rw-p 0 0 0\n")) == []


def test_maps_and_smaps_agree_on_which_lines_are_readable():
    # Same headers through both parsers: smaps only ever *narrows* the maps result.
    headers = "\n".join(l for l in SMAPS.splitlines() if "-" in l.split()[0])
    from_maps = set(_parse_proc_maps(headers))
    assert set(_parse_proc_smaps(SMAPS)) <= from_maps
