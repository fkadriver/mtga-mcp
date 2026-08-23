"""Tests for the interactive deck-wizard helpers (pure formatting + the Bo prompt)."""

from __future__ import annotations

from mtga_mcp import cli


def _scripted(steps):
    it = iter(steps)
    return lambda _prompt="": next(it)


def test_fmt_wildcards_orders_and_drops_zeroes():
    assert cli._fmt_wildcards({"uncommon": 1, "mythic": 3, "rare": 0}) == "3 mythic, 1 uncommon"
    assert cli._fmt_wildcards({}) == "none"


def test_prompt_best_of_parses_and_reprompts():
    assert cli._prompt_best_of(_scripted(["1"])) == 1
    assert cli._prompt_best_of(_scripted(["3"])) == 3
    assert cli._prompt_best_of(_scripted([""])) is None
    # junk then a valid answer: reprompts, doesn't crash
    assert cli._prompt_best_of(_scripted(["x", "2", "3"])) == 3
