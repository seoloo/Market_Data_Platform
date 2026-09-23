"""T/v1 추세 추종 시뮬레이터 합성 데이터 테스트 (15 사전등록)."""

from __future__ import annotations

import math

import pytest

from auto_trading.backtest.trend import (
    DailyBar,
    TrendConfig,
    simulate_trend,
    target_weight,
)

D = 86_400_000
START = 1_514_764_800_000  # 2018-01-01
FREE = TrendConfig(spot_cost=0.0, perp_cost=0.0)


def wave(n: int, drift: float, *, wobble: float = 0.01, base: float = 100.0) -> list[float]:
    """일정한 추세 + 작은 흔들림 (변동성이 0이 되지 않도록)."""
    return [base * math.exp(drift * k + wobble * math.sin(k)) for k in range(n)]


def to_bars(closes: list[float], first: int) -> dict[int, DailyBar]:
    return {first + k * D: DailyBar(c * 1.001, c) for k, c in enumerate(closes)}


def run(closes: list[float], cfg: TrendConfig = FREE, history: int = 130, funding=None):
    first = START - history * D
    bars = {"AUSDT": to_bars(closes, first)}
    return simulate_trend(
        bars,
        {"AUSDT": funding or []},
        from_ms=START,
        to_ms=first + (len(closes) - 1) * D,
        initial_equity=10_000.0,
        cfg=cfg,
    )


class TestTargetWeight:
    def test_direction_is_mean_of_lookback_signs(self) -> None:
        up = wave(200, 0.005)
        assert target_weight(up, FREE) == pytest.approx(min(0.15 / _vol(up), 0.5))
        down = wave(200, -0.005)
        assert target_weight(down, FREE) == pytest.approx(-min(0.15 / _vol(down), 0.5))

    def test_mixed_signs_give_one_third(self) -> None:
        closes = wave(200, 0.005)
        closes[-21:] = [closes[-22] * (1 - 0.001 * k) for k in range(1, 22)]  # 최근 20일만 하락
        weight = target_weight(closes, FREE)
        assert weight is not None
        assert weight > 0
        assert weight == pytest.approx(min(0.15 / _vol(closes), 0.5) / 3)

    def test_needs_enough_history(self) -> None:
        assert target_weight(wave(120, 0.01), FREE) is None

    def test_long_only_turns_short_into_cash(self) -> None:
        cfg = TrendConfig(long_only=True)
        assert target_weight(wave(200, -0.005), cfg) == 0.0

    def test_weight_is_capped(self) -> None:
        calm = wave(200, 0.0005, wobble=0.0001)
        assert target_weight(calm, FREE) == pytest.approx(0.5)


class TestSimulation:
    def test_uptrend_goes_long_with_one_day_lag(self) -> None:
        result = run(wave(260, 0.004))
        assert result.days_long["AUSDT"] > 0
        assert result.days_short["AUSDT"] == 0
        assert result.returns[0] == 0.0  # 첫날은 목표만 정하고 체결은 다음 날
        assert result.long_pnl > 0

    def test_downtrend_short_receives_positive_funding(self) -> None:
        closes = wave(260, -0.004)
        first = START - 130 * D
        funding = [(first + k * D + 3, 0.0001) for k in range(len(closes))]
        result = run(closes, funding=funding)
        assert result.days_short["AUSDT"] > 0
        assert result.funding > 0
        assert result.short_pnl > 0

    def test_costs_follow_leg_type(self) -> None:
        up = run(wave(200, 0.004), TrendConfig(perp_cost=0.0))
        assert up.costs > 0  # 롱은 현물 비용
        up_free = run(wave(200, 0.004), TrendConfig(spot_cost=0.0))
        assert up_free.costs == 0.0

    def test_band_limits_trading(self) -> None:
        loose = run(wave(260, 0.004), TrendConfig(band=0.0))
        tight = run(wave(260, 0.004), TrendConfig(band=0.05))
        assert tight.trades < loose.trades

    def test_short_liquidated_when_price_doubles_intraday(self) -> None:
        closes = wave(200, -0.004)
        first = START - 130 * D
        bars = {"AUSDT": to_bars(closes, first)}
        spike_day = first + 190 * D
        bars["AUSDT"][spike_day] = DailyBar(closes[190] * 2.2, closes[190])
        result = simulate_trend(
            bars, {}, from_ms=START, to_ms=first + 199 * D, initial_equity=1e4, cfg=FREE
        )
        assert result.liquidations == 1

    def test_missing_bar_raises(self) -> None:
        closes = wave(200, 0.004)
        first = START - 130 * D
        bars = {"AUSDT": to_bars(closes, first)}
        del bars["AUSDT"][START + 5 * D]
        with pytest.raises(ValueError, match="결측"):
            simulate_trend(
                bars, {}, from_ms=START, to_ms=first + 199 * D, initial_equity=1e4, cfg=FREE
            )


def _vol(closes: list[float]) -> float:
    import statistics

    steps = [math.log(closes[-k] / closes[-k - 1]) for k in range(1, 61)]
    return statistics.stdev(steps) * math.sqrt(365)
