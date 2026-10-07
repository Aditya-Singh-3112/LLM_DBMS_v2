"""The comparison logic of scripts/eval_agent.py (no LLM involved)."""
from decimal import Decimal

from scripts.eval_agent import _same_rows


def test_numbers_compare_by_value():
    assert _same_rows([[3, "2.50"]], [[Decimal("3.000"), 2.5]], ordered=False)


def test_unordered_unless_requested():
    assert _same_rows([["a"], ["b"]], [["b"], ["a"]], ordered=False)
    assert not _same_rows([["a"], ["b"]], [["b"], ["a"]], ordered=True)


def test_extra_columns_allowed_unless_strict():
    expected, actual = [["Mumbai"], ["Pune"]], [["Mumbai", 2], ["Pune", 2]]
    assert _same_rows(expected, actual, ordered=False)
    assert not _same_rows(expected, actual, ordered=False, allow_extra_columns=False)
    # Columns may come back in another order.
    assert _same_rows([["x", 1]], [[1, "x"]], ordered=False)


def test_row_count_and_values_must_match():
    assert not _same_rows([["a"]], [["a"], ["a"]], ordered=False)
    assert not _same_rows([["a"]], [["b", "a2"]], ordered=False)
    assert _same_rows([], [], ordered=False)
