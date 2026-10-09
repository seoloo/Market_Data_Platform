from __future__ import annotations

import pytest
from scripts.run_early_leader_191 import first_entries, forward_fill, fwd_return


def test_first_entries_needs_gap() -> None:
    lists = [["A"]] * 3 + [[]] * 20 + [["A", "B"]]
    assert first_entries(lists, gap=20) == [(23, "A"), (23, "B")]
    assert first_entries([["A"]] * 25, gap=20) == []


def test_forward_return_uses_last_close_when_delisted() -> None:
    xs = [10.0, 11.0, None, 12.0, None, None]
    assert fwd_return(xs, 1, 5) == pytest.approx(12 / 11 - 1)
    assert forward_fill(xs) == [10.0, 11.0, 11.0, 12.0, 12.0, 12.0]
