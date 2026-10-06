from __future__ import annotations

import pytest

from auto_trading.research.kr_total_return import business_days, cum_index, total_return_index


def test_year_end_record_date_uses_settlement_on_closed_weekday() -> None:
    trading = ["20231226", "20231227", "20231228", "20240102", "20240103"]
    biz = business_days(trading)
    assert "20231229" in biz  # 휴장이지만 결제는 되는 평일
    assert trading[cum_index(trading, biz, "20231231") or 0] == "20231227"  # 12-28 배당락


def test_trading_day_and_weekend_record_dates() -> None:
    trading = ["20240326", "20240327", "20240328", "20240329", "20240401", "20240402"]
    biz = business_days(trading)
    assert trading[cum_index(trading, biz, "20240329") or 0] == "20240327"  # R−2
    feb = ["20260224", "20260225", "20260226", "20260227", "20260302", "20260303"]
    assert feb[cum_index(feb, business_days(feb), "20260228") or 0] == "20260225"  # 토요일 기준일
    assert cum_index(trading, biz, "20200101") is None


def test_total_return_adds_dividend_over_previous_market_cap() -> None:
    tr = total_return_index([100.0, 100.0, 110.0], {1: 20.0}, [1000.0, 1000.0, 1000.0])
    assert tr == pytest.approx([1.0, 1.02, 1.02 * 1.1])
