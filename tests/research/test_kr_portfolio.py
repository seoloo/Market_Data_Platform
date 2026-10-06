from __future__ import annotations

import pytest

from auto_trading.research.kr_portfolio import (
    StockDays,
    buy_limit,
    sell_limit,
    simulate,
    tick_size,
)

DAYS = [f"2022{m:02d}{d:02d}" for m in (1, 2) for d in range(1, 29)]
N = len(DAYS)


def _flat(px: float = 10_000.0, n: int = N, **kw: object) -> StockDays:
    base: dict[str, object] = {
        "open": [px] * n,
        "high": [px * 1.01] * n,
        "low": [px * 0.99] * n,
        "close": [px] * n,
        "volume": [1e9] * n,
        "value": [1e13] * n,
        "factor": [1.0] * n,
        "last": n - 1,
        "delisted": False,
    }
    base.update(kw)
    return StockDays(**base)  # type: ignore[arg-type]


def _no_cost(day: str, side: str) -> float:
    return 0.0


def test_tick_sizes_change_in_2023() -> None:
    assert tick_size(15_000, "20221230") == 50 and tick_size(15_000, "20230126") == 10
    assert tick_size(150_000, "20221230") == 500 and tick_size(150_000, "20230126") == 100
    assert buy_limit(10_000, "20221230") == 10_050
    assert sell_limit(10_000, "20221230") == 9_950


def test_buys_next_day_at_min_of_limit_and_high_with_integer_shares() -> None:
    res = simulate(DAYS, {"A": _flat()}, {0: {"A": 0.5}}, _no_cost, 1_000_000, start=1, end=5)
    # 1 일에 50 주(=0.5 × 100 만 ÷ 1 만) 를 min(10,050, 10,100) = 10,050 에 산다
    assert res.fills == 1
    assert res.equity[0] == pytest.approx(1_000_000 - 50 * 10_050 + 50 * 10_000)


def test_no_fill_when_close_is_not_below_limit_and_retries_stop_after_five() -> None:
    up = _flat(close=[10_000.0] + [10_000.0 * 1.006**k for k in range(1, N)])
    res = simulate(DAYS, {"A": up}, {0: {"A": 0.5}}, _no_cost, 1_000_000, start=1, end=10)
    assert res.fills == 0
    assert res.unfilled_buy_days == 5


def test_sale_cash_arrives_two_days_later_and_costs_apply() -> None:
    targets = {0: {"A": 1.0}, 3: {}}
    res = simulate(DAYS, {"A": _flat()}, targets, lambda d, s: 0.01, 1_000_000, start=1, end=8)
    assert res.fills == 2  # 1 일 매수 · 4 일 매도
    assert res.costs > 0
    assert res.exposure[3] == pytest.approx(0.0)  # 4 일(색인 3) 판 뒤 노출 0 (결제 대기)


def test_dividend_entitlement_on_cum_day_and_paid_later() -> None:
    s = _flat(dividends={3: (100.0, 6)})
    res = simulate(DAYS, {"A": s}, {0: {"A": 0.5}}, _no_cost, 1_000_000, start=1, end=8)
    assert res.dividends == pytest.approx(50 * 100.0)


def test_split_multiplies_shares_by_factor_ratio() -> None:
    close = [10_000.0] * 5 + [2_000.0] * (N - 5)
    s = _flat(close=close, high=[c * 1.01 for c in close], low=[c * 0.99 for c in close],
              factor=[5.0] * 5 + [1.0] * (N - 5))  # fmt: skip
    res = simulate(DAYS, {"A": s}, {0: {"A": 0.5}}, _no_cost, 1_000_000, start=1, end=8)
    assert res.equity[-1] == pytest.approx(res.equity[2], rel=1e-9)  # 분할로 평가액 안 바뀜


def test_delisting_exits_at_last_close() -> None:
    s = _flat(last=4, delisted=True)
    res = simulate(DAYS, {"A": s}, {0: {"A": 0.5}}, _no_cost, 1_000_000, start=1, end=8)
    assert res.delist_exits == 1
    assert res.exposure[-1] == pytest.approx(0.0)


def test_delay_shifts_first_order_by_one_day() -> None:
    a = simulate(DAYS, {"A": _flat()}, {0: {"A": 0.5}}, _no_cost, 1_000_000, start=1, end=3)
    b = simulate(
        DAYS, {"A": _flat()}, {0: {"A": 0.5}}, _no_cost, 1_000_000, start=1, end=3, delay=1
    )
    assert a.exposure[0] > 0 and b.exposure[0] == 0 and b.exposure[1] > 0
