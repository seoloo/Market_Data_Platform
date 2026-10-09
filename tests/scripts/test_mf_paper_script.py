from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import scripts.mf_paper as mod
from scripts.mf_paper import BOOKS, _filled


class _Fixed(datetime):
    """시험 날짜를 2026-10-13 23:00 UTC 로 고정 (10-12 봉까지 끝난 뒤)."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return datetime(2026, 10, 13, 23, 0, tzinfo=UTC)


def test_filled_uses_first_symbol_calendar_and_carries_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "datetime", _Fixed)
    px = {
        "A": {"2026-10-07": 1.0, "2026-10-08": 2.0, "2026-10-12": 3.0},
        "B": {"2026-10-06": 9.0, "2026-10-08": 8.0, "2026-10-09": 7.0},
    }
    out = _filled(px, ["A", "B"], 21)
    assert list(out) == ["2026-10-07", "2026-10-08", "2026-10-12"]
    assert out["2026-10-07"] == {"A": 1.0, "B": 9.0}
    assert out["2026-10-12"] == {"A": 3.0, "B": 7.0}


def test_filled_drops_today_before_close(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Morning(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return datetime(2026, 10, 12, 10, 0, tzinfo=UTC)  # 그날 장 마감(21 시 UTC) 전

    monkeypatch.setattr(mod, "datetime", _Morning)
    px = {"A": {"2026-10-08": 2.0, "2026-10-12": 3.0}}
    assert list(_filled(px, ["A"], 21)) == ["2026-10-08"]


def test_books_targets_sum_to_one() -> None:
    for cfg in BOOKS.values():
        assert abs(sum(cfg["targets"].values()) - 1.0) < 1e-12
