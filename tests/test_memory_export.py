"""Tests for the pure block-extraction/scoring logic of the memory scanner.

These exercise the risky parsing without a live MTGA process: `_extract` walks a synthetic
little-endian (id, qty) buffer, and `_select_best`/`_validate` score dict blocks.
"""

from __future__ import annotations

import struct

from mtga_mcp.anchors import Anchor
from mtga_mcp.memory_export import MemoryScanner, ScanConfig


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
