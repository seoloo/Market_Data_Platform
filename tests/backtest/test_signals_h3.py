"""H3 교차 심볼 신호 계산 테스트.

특히 **인과성**을 고정한다 — 아직 정산되지 않은 funding 을 당겨 쓰거나 warm-up 이
모자란 구간에 값을 만들면, 그 뒤 모든 결과가 조용히 거짓이 된다.
"""

from __future__ import annotations

import pytest

from auto_trading.backtest.signals_h3 import (
    BARS_PER_HOUR,
    REVERSAL_BARS,
    SIGNAL_NAMES,
    Z_LOOKBACK,
    build_cross_signals,
)

STEP = 300_000
BASE = 1_672_531_200_000  # 2023-01-01 00:00 UTC
WARMUP = Z_LOOKBACK + BARS_PER_HOUR + 1


def metrics(
    count: int, *, oi_growth: float = 0.0, taker: float = 1.0
) -> dict[int, tuple[float, float, float, float]]:
    """``count`` 개의 5분 스냅샷. 값은 기본적으로 평탄하다."""
    return {
        BASE + i * STEP: (
            1000.0 * (1.0 + oi_growth) ** i,
            taker,
            2.0,
            1.5,
        )
        for i in range(count)
    }


def closes(count: int, *, start: float = 100.0, step: float = 0.0) -> dict[int, float]:
    return {BASE + i * STEP: start + step * i for i in range(count)}


class TestWarmup:
    def test_short_history_yields_nothing(self) -> None:
        """warm-up 이 모자라면 0 으로 채우지 않고 **비운다**."""
        out = build_cross_signals(closes(100), metrics(100), {})
        assert all(series == {} for series in out.values())

    def test_all_five_signals_are_present_in_the_result(self) -> None:
        out = build_cross_signals(closes(50), metrics(50), {})
        assert set(out) == set(SIGNAL_NAMES)

    def test_values_start_only_after_the_causal_window(self) -> None:
        count = WARMUP + 50
        out = build_cross_signals(closes(count), metrics(count, taker=1.0), {})
        first = min(out["X3_rel_taker"])
        assert first >= BASE + (Z_LOOKBACK - 1) * STEP


class TestFundingCausality:
    def _funding(self) -> dict[int, float]:
        settle = BASE + 100 * STEP
        return {settle: 0.0005, settle + 8 * 3_600_000: -0.0003}

    @pytest.mark.leakage
    def test_rate_is_not_used_before_it_settles(self) -> None:
        count = WARMUP + 200
        out = build_cross_signals(closes(count), metrics(count), self._funding())
        settle = BASE + 100 * STEP
        assert all(at_ms >= settle for at_ms in out["X1_rel_funding"])

    @pytest.mark.leakage
    def test_the_settled_rate_is_carried_forward_not_the_next_one(self) -> None:
        count = WARMUP + 200
        out = build_cross_signals(closes(count), metrics(count), self._funding())
        settle = BASE + 100 * STEP
        just_before_next = settle + 8 * 3_600_000 - STEP
        value = out["X1_rel_funding"].get(just_before_next)
        assert value == pytest.approx(0.0005), "다음 정산 요율을 당겨 썼다"

    def test_no_funding_history_means_no_signal(self) -> None:
        count = WARMUP + 50
        out = build_cross_signals(closes(count), metrics(count), {})
        assert out["X1_rel_funding"] == {}


class TestOpenInterest:
    def test_steady_growth_gives_no_anomaly(self) -> None:
        """일정한 증가율은 z-score 가 0 이어야 한다 — 이례성을 재는 지표이기 때문."""
        count = WARMUP + 100
        out = build_cross_signals(closes(count), metrics(count, oi_growth=0.001), {})
        last = max(out["X2_rel_oi_change"])
        assert out["X2_rel_oi_change"][last] == pytest.approx(0.0, abs=1e-6)

    def test_sudden_surge_is_flagged(self) -> None:
        count = WARMUP + 100
        rows = metrics(count)
        spike_at = BASE + (count - 1) * STEP
        rows[spike_at] = (rows[spike_at][0] * 1.5, 1.0, 2.0, 1.5)
        out = build_cross_signals(closes(count), rows, {})
        assert out["X2_rel_oi_change"][spike_at] > 3.0


class TestCrowdGap:
    def test_gap_is_the_difference_of_two_z_scores(self) -> None:
        count = WARMUP + 100
        rows = metrics(count)
        # 군중만 급격히 롱으로 쏠린다 → 상위 트레이더와의 괴리가 양수
        last = BASE + (count - 1) * STEP
        rows[last] = (rows[last][0], 1.0, 4.0, 1.5)
        out = build_cross_signals(closes(count), rows, {})
        assert out["X4_rel_crowd_gap"][last] > 0


class TestReversal:
    def test_past_return_uses_the_configured_lookback(self) -> None:
        count = WARMUP + 200
        prices = closes(count, start=100.0, step=0.01)
        out = build_cross_signals(prices, metrics(count), {})
        at_ms = BASE + (count - 1) * STEP
        expected = (
            (prices[at_ms] - prices[at_ms - REVERSAL_BARS * STEP])
            / prices[at_ms - REVERSAL_BARS * STEP]
            * 10_000
        )
        assert out["X5_rel_reversal"][at_ms] == pytest.approx(expected)

    def test_missing_past_price_yields_no_value(self) -> None:
        count = WARMUP + 100
        prices = closes(count)
        for i in range(0, 80):  # 과거 구간을 비운다
            prices.pop(BASE + i * STEP, None)
        out = build_cross_signals(prices, metrics(count), {})
        assert BASE + 79 * STEP not in out["X5_rel_reversal"]


class TestIndependenceFromUniverse:
    def test_signals_use_only_this_symbols_own_history(self) -> None:
        """횡단면 비교는 다음 단계의 일이다. 여기서는 심볼 하나만 본다."""
        count = WARMUP + 100
        first = build_cross_signals(closes(count), metrics(count), {})
        second = build_cross_signals(closes(count), metrics(count), {})
        assert first == second
