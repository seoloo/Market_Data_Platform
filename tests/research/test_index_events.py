"""문서 208 — S&P 편입 · 편출 사건 창: 체결 시점 · 벤치마크 정렬 · 날짜 묶음."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from auto_trading.research.index_events import (
    Px,
    addition_window,
    by_date_mean,
    deletion_window,
    load_px,
    t_stat,
)

D0 = date(2024, 3, 1)  # 금요일


def _px(prices: list[tuple[float, float]], start: date = D0) -> Px:
    days: list[date] = []
    d = start
    while len(days) < len(prices):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return Px(days, [p[0] for p in prices], [p[1] for p in prices])


def test_addition_buys_next_open_and_sells_before_effective_close() -> None:
    # 거래일: 3/1 금 · 3/4 월 · 3/5 화 · 3/6 수 · 3/7 목
    stock = _px([(10, 10), (11, 12), (12, 13), (13, 14), (20, 20)])
    spy = _px([(100, 100), (100, 101), (101, 102), (102, 103), (103, 104)])
    w = addition_window(stock, spy, announced=date(2024, 3, 1), effective=date(2024, 3, 7))
    assert w is not None
    assert (w.start, w.end) == (date(2024, 3, 4), date(2024, 3, 6))
    assert w.ret == pytest.approx(14 / 11 - 1)  # 3/7 의 20 은 쓰지 않는다
    assert w.bench == pytest.approx(103 / 100 - 1)
    assert w.excess == pytest.approx(w.ret - w.bench)


def test_addition_none_when_no_trading_day_between() -> None:
    stock = _px([(10, 10), (11, 12), (12, 13)])
    spy = _px([(100, 100), (100, 101), (101, 102)])
    assert addition_window(stock, spy, date(2024, 3, 1), date(2024, 3, 4)) is None


@pytest.mark.leakage
def test_addition_window_ignores_prices_from_effective_day_on() -> None:
    base = [(10.0, 10.0), (11.0, 12.0), (12.0, 13.0), (13.0, 14.0)]
    spy = _px([(100.0, 100.0)] * 8)
    a = addition_window(_px([*base, (20.0, 20.0)]), spy, D0, date(2024, 3, 7))
    b = addition_window(_px([*base, (99.0, 1.0)]), spy, D0, date(2024, 3, 7))
    assert a == b


def test_deletion_buys_close_before_effective_and_holds() -> None:
    stock = _px([(10, 10), (9, 8), (8, 9), (9, 9.5), (9.5, 10)])
    spy = _px([(100, 100), (100, 100), (100, 101), (101, 101), (101, 102)])
    w = deletion_window(stock, spy, effective=date(2024, 3, 5), hold=2)
    assert w is not None
    assert (w.start, w.end) == (date(2024, 3, 4), date(2024, 3, 6))
    assert w.ret == pytest.approx(9.5 / 8 - 1)
    assert w.bench == pytest.approx(101 / 100 - 1)
    assert deletion_window(stock, spy, effective=date(2024, 3, 5), hold=9) is None


def test_by_date_mean_and_t() -> None:
    units = by_date_mean([(D0, 0.02), (D0, 0.0), (date(2024, 3, 4), -0.01)])
    assert units == [(D0, 0.01), (date(2024, 3, 4), -0.01)]
    assert t_stat([0.01, 0.01]) == 0.0
    assert t_stat([0.01, 0.03, 0.02]) > 0


def test_load_px_skips_bad_rows() -> None:
    rows = [
        {"date": "2024-03-01", "open": "10", "close": "11"},
        {"date": "2024-03-01", "open": "10", "close": "11"},
        {"date": "2024-03-04", "open": "", "close": "11"},
        {"date": "2024-03-05", "open": "0", "close": "11"},
        {"date": "2024-03-06", "open": "12", "close": "13"},
    ]
    p = load_px(rows)
    assert p.days == [date(2024, 3, 1), date(2024, 3, 6)]
