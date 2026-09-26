"""스테이킹 캐리 계산 (문서 92) — 합성 자료만. 실제 수익 미계산."""

from __future__ import annotations

import pytest

from auto_trading.research.staking_carry import (
    DAY_MS,
    HOUR_MS,
    Bar,
    CarryConfig,
    Funding,
    longest_negative_funding_days,
    mdd,
    simulate_carry,
)

T0 = 1_726_185_600_000  # 2024-09-13 00:00 UTC (금요일)
FREE = CarryConfig(spot_cost=0.0, perp_cost=0.0)


def flat(hours: int, price: float = 100.0) -> list[Bar]:
    return [Bar(T0 + h * HOUR_MS, price, price, price) for h in range(hours)]


def fund(hours: int, rate: float, price: float = 100.0) -> list[Funding]:
    return [Funding(T0 + h * HOUR_MS, rate, price) for h in range(8, hours + 1, 8)]


def test_flat_price_positive_funding_accrues_to_short() -> None:
    bars = flat(24)
    res = simulate_carry(
        bars, fund(24, 0.0001), lambda _t: 1.0, start_price=(100.0, 100.0), cfg=FREE
    )
    # 숏 50 코인 × 100 × 0.0001 × 3 회
    assert res.funding == pytest.approx(50 * 100 * 0.0001 * 3)
    assert res.final_equity == pytest.approx(10_000 + res.funding)
    assert res.liquidated_ms is None and res.min_buffer == pytest.approx(1.0, rel=1e-3)


def test_negative_funding_is_paid() -> None:
    res = simulate_carry(
        flat(24), fund(24, -0.0002), lambda _t: 1.0, start_price=(100.0, 100.0), cfg=FREE
    )
    assert res.funding < 0 and res.final_equity < 10_000


def test_staking_adds_half_of_ratio_growth_on_flat_price() -> None:
    days = 14
    bars = flat(days * 24)

    def ratio(t: int) -> float:
        return 1.0 + 0.001 * ((t - T0) // DAY_MS)  # 매일 0.1%

    c = simulate_carry(bars, [], ratio, start_price=(100.0, 100.0), cfg=FREE)
    b0 = simulate_carry(bars, [], lambda _t: 1.0, start_price=(100.0, 100.0), cfg=FREE)
    assert b0.final_equity == pytest.approx(10_000)
    # 코인 절반 × 비율 증가 — 주간 조정으로 약간의 복리. 1.3% 근처
    growth = c.final_equity / 10_000 - 1
    assert 0.5 * 0.013 * 0.99 < growth < 0.5 * 0.0135 * 1.01


def test_weekly_rebalance_on_mondays_and_costs_counted() -> None:
    bars = flat(15 * 24)
    res = simulate_carry(bars, [], lambda _t: 1.0, start_price=(100.0, 100.0), cfg=CarryConfig())
    assert res.weekly_rebalances == 2  # 9/16, 9/23 월요일
    entry = 0.0015 * 5_000 + 0.00095 * 50 * 100
    exit_fee = 0.0015 * res.final_equity * 0.5 + 0.00095 * 50 * 100
    assert res.costs == pytest.approx(entry + exit_fee, rel=0.01)


def test_slow_rally_triggers_emergency_not_liquidation() -> None:
    prices = [100.0 * (1.004**h) for h in range(200)]  # 200 시간에 약 2.2 배
    bars = [Bar(T0 + h * HOUR_MS, p, p, p) for h, p in enumerate(prices)]
    res = simulate_carry(bars, [], lambda _t: 1.0, start_price=(100.0, 100.0), cfg=FREE)
    assert res.liquidated_ms is None and res.emergency_rebalances >= 1
    assert res.final_equity == pytest.approx(10_000, rel=1e-6)  # 가격 중립 (비용 0)


def test_one_hour_spike_is_liquidation() -> None:
    bars = flat(5)
    bars[3] = Bar(bars[3].open_ms, 100.0, 100.0, 199.5)  # 1 시간 안에 고가 거의 2 배
    res = simulate_carry(bars, [], lambda _t: 1.0, start_price=(100.0, 100.0), cfg=FREE)
    assert res.liquidated_ms == bars[3].open_ms + HOUR_MS


def test_helpers() -> None:
    assert mdd([(1, 100.0), (2, 80.0), (3, 120.0), (4, 90.0)])[0] == pytest.approx(0.25)
    events = [Funding(T0 + d * DAY_MS, -0.0001 if d >= 10 else 0.0001, 1.0) for d in range(60)]
    days, _end = longest_negative_funding_days(events, window_days=30)
    assert days > 0
