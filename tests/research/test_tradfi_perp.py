"""문서 86 §6 — TradFi 무기한 실행 가능성 계산(순수 함수)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from scripts.measure_tradfi_perp import (
    book_cost_bps,
    funding_annual,
    judge,
    premium_summary,
    us_regular_session,
)


def _ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def test_us_session_follows_new_york_time() -> None:
    assert us_regular_session(_ms(2026, 9, 28, 13, 30))  # 월 09:30 EDT
    assert not us_regular_session(_ms(2026, 9, 28, 13, 29))
    assert not us_regular_session(_ms(2026, 9, 26, 15, 0))  # 토요일
    assert us_regular_session(_ms(2026, 12, 7, 14, 30))  # 겨울 09:30 EST


def test_book_cost_walks_levels_and_reports_shortfall() -> None:
    asks = [["100.1", "50"], ["100.3", "100"]]
    bids = [["99.9", "10"]]
    cost = book_cost_bps(asks, bids, 10_000)
    assert cost["spread_bps"] == pytest.approx(20.0)
    # 50 개 @100.1 = 5,005, 나머지 4,995 @100.3
    qty = 50 + 4_995 / 100.3
    assert cost["buy_bps"] == pytest.approx((10_000 / qty / 100 - 1) * 1e4)
    assert cost["sell_bps"] is None  # 매수 호가 999 USDT 뿐


def test_funding_annual_sums_over_span() -> None:
    day = 86_400_000
    rates = [(k * day // 3, 0.0001) for k in range(3 * 365 + 1)]
    assert funding_annual(rates) == pytest.approx(0.0001 * (3 * 365 + 1) * 365.25 / 365)
    assert funding_annual([(0, 0.1)]) is None


def test_premium_splits_session_and_closed_hours() -> None:
    rows = [(_ms(2026, 9, 28, 14, 0), 0.0002), (_ms(2026, 9, 26, 14, 0), -0.0010)]
    s = premium_summary(rows)
    assert s["session_median_bps"] == pytest.approx(2.0)
    assert s["closed_median_bps"] == pytest.approx(10.0)


def test_judge_needs_enough_session_snapshots() -> None:
    hist = {
        "SPYUSDT": {"funding_annual": 0.01, "premium": {"median_bps": 3.0}, "demo_listed": True}
    }
    few = [
        {
            "symbol": "SPYUSDT",
            "us_session": True,
            "at_ms": _ms(2026, 9, 28, 14),
            "buy_bps": 1.0,
            "sell_bps": 1.0,
        }
    ]
    r = judge(few, hist)["SPYUSDT"]
    assert r["criteria"]["C1"][0] == "자료 부족"
    assert r["overall"] == "판정 대기"
    many = [
        {
            "symbol": "SPYUSDT",
            "us_session": True,
            "at_ms": _ms(2026, 10, 5 + d // 2, 14),
            "buy_bps": 1.0,
            "sell_bps": 2.0,
        }
        for d in range(10)
    ]
    assert judge(many, hist)["SPYUSDT"]["overall"] == "가능"
    hist["SPYUSDT"]["funding_annual"] = 0.05
    assert judge(many, hist)["SPYUSDT"]["overall"] == "불가"
    assert judge(many, {})["XAUUSDT"]["criteria"]["C4"][0] == "자료 부족"
