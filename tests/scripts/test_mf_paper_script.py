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


def test_tri3_trend_targets_and_cash() -> None:
    from scripts.mf_paper import trend3_targets, trend_on

    days = [f"2026-{m:02d}-{d:02d}" for m in range(1, 10) for d in range(1, 29)]
    up = {d: 100.0 + i for i, d in enumerate(days)}
    down = {d: 400.0 - i for i, d in enumerate(days)}
    hist = {"QQQ": up, "GLDM": down, "IEF": dict.fromkeys(days[:50], 1.0)}
    tg = trend3_targets(hist, "2026-10-01")
    assert tg["QQQ"] == 1 / 3
    assert "GLDM" not in tg and "IEF" not in tg  # 하락 · 이력 부족
    assert abs(tg["CASH"] - 2 / 3) < 1e-12
    assert trend_on([1.0] * 50) is None


def test_tri3_book_keeps_cash_value() -> None:
    from auto_trading.ops.mf_paper import mark, open_book, rebalance

    book = open_book(1000.0, {"QQQ": 1 / 3, "CASH": 2 / 3}, {"QQQ": 10.0, "CASH": 1.0}, 0.0)
    assert abs(mark(book, "d1", {"QQQ": 10.0, "CASH": 1.0}) - 1000.0) < 1e-9
    rebalance(book, {"CASH": 1.0}, {"QQQ": 20.0, "CASH": 1.0}, 0.0)
    assert abs(mark(book, "d2", {"QQQ": 5.0, "CASH": 1.0}) - (1000 / 3 * 2 + 2000 / 3)) < 1e-9
