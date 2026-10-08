from __future__ import annotations

from scripts.mf_paper import BOOKS, _filled


def test_filled_uses_first_symbol_calendar_and_carries_gaps() -> None:
    px = {
        "A": {"2026-10-07": 1.0, "2026-10-08": 2.0, "2026-10-12": 3.0},
        "B": {"2026-10-06": 9.0, "2026-10-08": 8.0, "2026-10-09": 7.0},
    }
    out = _filled(px, ["A", "B"], 21)
    assert list(out) == ["2026-10-07", "2026-10-08", "2026-10-12"]
    assert out["2026-10-07"] == {"A": 1.0, "B": 9.0}
    assert out["2026-10-12"] == {"A": 3.0, "B": 7.0}


def test_books_targets_sum_to_one() -> None:
    for cfg in BOOKS.values():
        assert abs(sum(cfg["targets"].values()) - 1.0) < 1e-12
