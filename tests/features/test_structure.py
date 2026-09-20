"""스윙 검출 테스트.

**핵심은 repainting 차단이다.** 잘린 데이터로 돌린 결과와 전체 데이터로 돌린 뒤
``confirmed_as_of`` 로 거른 결과가 **정확히 같아야** 한다.

이 성질이 깨지면 백테스트가 "그때는 알 수 없던 스윙"을 쓰게 되고,
손절 위치가 실거래와 달라진다 (06-scoring-engine.md §7).
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features.structure import (
    StructureLabel,
    Swing,
    SwingKind,
    SwingSeries,
    detect_swings,
)

D = Decimal
TF = Timeframe.H1
PCT2 = D("0.02")


def bar(index: int, low: str, high: str, close: str | None = None) -> Candle:
    """저가·고가를 지정한 봉. open/close 는 범위 안에 둔다."""
    lo, hi = D(low), D(high)
    c = D(close) if close is not None else hi
    return Candle(
        open_ms=index * TF.ms,
        close_ms=index * TF.ms + TF.ms - 1,
        open=lo,
        high=hi,
        low=lo,
        close=c,
        volume=D(1),
    )


def ramp(start: int, count: int, step: int, index_from: int = 0) -> list[Candle]:
    """일정 폭으로 오르내리는 봉들. 각 봉의 high/low 폭은 step 만큼."""
    out: list[Candle] = []
    price = start
    for i in range(count):
        low = min(price, price + step)
        high = max(price, price + step)
        out.append(bar(index_from + i, str(low), str(high), str(price + step)))
        price += step
    return out


# --------------------------------------------------------------- repainting


@pytest.mark.leakage
class TestNoRepainting:
    """확정된 스윙은 절대 바뀌지 않는다."""

    def _zigzag(self) -> list[Candle]:
        # 100 -> 120 -> 105 -> 130 -> 110 (2% 임계를 충분히 넘는 진폭)
        candles = ramp(100, 20, 1)
        candles += ramp(120, 15, -1, index_from=20)
        candles += ramp(105, 25, 1, index_from=35)
        candles += ramp(130, 20, -1, index_from=60)
        return candles

    def test_truncated_run_matches_filtered_full_run(self):
        """가장 중요한 테스트.

        as_of 시점까지의 데이터만으로 돌린 결과 == 전체로 돌린 뒤 as_of 로 거른 결과
        """
        candles = self._zigzag()
        full = detect_swings(candles, PCT2)

        for i in range(1, len(candles) + 1):
            as_of = candles[i - 1].close_ms
            truncated = detect_swings(candles[:i], PCT2)
            filtered = full.confirmed_as_of(as_of)
            assert truncated.swings == filtered.swings, f"i={i} 에서 불일치"

    def test_appending_future_never_changes_past_swings(self):
        candles = self._zigzag()
        before = detect_swings(candles[:50], PCT2)
        after = detect_swings(candles, PCT2)
        as_of = candles[49].close_ms
        assert before.swings == after.confirmed_as_of(as_of).swings

    def test_extreme_future_spike_does_not_rewrite_history(self):
        """미래에 폭등이 와도 이미 확정된 고점은 그대로여야 한다.

        일반적인 ZigZag 는 여기서 이전 스윙을 덮어써 repainting 을 일으킨다.
        """
        candles = self._zigzag()
        confirmed = detect_swings(candles, PCT2)
        spike = [*candles, bar(200, "500", "600", "600")]
        after = detect_swings(spike, PCT2)
        assert after.swings[: len(confirmed.swings)] == confirmed.swings

    @settings(max_examples=40, deadline=None)
    @given(steps=st.lists(st.integers(-8, 8), min_size=30, max_size=90))
    def test_property_truncation_consistency(self, steps):
        price = 1000
        candles: list[Candle] = []
        for i, step in enumerate(steps):
            nxt = max(10, price + step)
            candles.append(bar(i, str(min(price, nxt)), str(max(price, nxt)), str(nxt)))
            price = nxt

        full = detect_swings(candles, PCT2)
        midpoint = len(candles) // 2
        truncated = detect_swings(candles[:midpoint], PCT2)
        as_of = candles[midpoint - 1].close_ms
        assert truncated.swings == full.confirmed_as_of(as_of).swings


# --------------------------------------------------------------- 확정 지연


class TestConfirmationLag:
    def test_confirmation_is_always_later_than_extreme(self):
        candles = ramp(100, 20, 1) + ramp(120, 20, -1, index_from=20)
        for swing in detect_swings(candles, PCT2).swings:
            assert swing.confirmed_at_ms > swing.at_ms
            assert swing.confirmation_lag_ms > 0

    def test_swing_rejects_confirmation_before_extreme(self):
        with pytest.raises(ValueError, match="확정 시각이 극값 시각보다 빠르다"):
            Swing(kind=SwingKind.HIGH, price=D(100), at_ms=1000, confirmed_at_ms=500)

    def test_swing_rejects_non_positive_price(self):
        with pytest.raises(ValueError, match="0 이하"):
            Swing(kind=SwingKind.HIGH, price=D(0), at_ms=0, confirmed_at_ms=1)

    def test_lag_grows_with_threshold(self):
        """임계가 클수록 확정이 늦어진다 — 그만큼 더 기다려야 한다."""
        candles = ramp(100, 30, 1) + ramp(130, 30, -1, index_from=30)
        tight = detect_swings(candles, D("0.01")).last(SwingKind.HIGH)
        loose = detect_swings(candles, D("0.10")).last(SwingKind.HIGH)
        assert tight is not None
        assert loose is not None
        assert loose.confirmation_lag_ms > tight.confirmation_lag_ms

    def test_pending_extreme_is_not_emitted(self):
        """진행 중인 극값은 내보내지 않는다. 지금은 그것이 극값인지 알 수 없다."""
        rising_only = ramp(100, 40, 1)
        series = detect_swings(rising_only, PCT2)
        assert series.of_kind(SwingKind.HIGH) == ()


# --------------------------------------------------------------- 검출


class TestDetection:
    def test_empty_input(self):
        assert len(detect_swings([], PCT2)) == 0

    def test_flat_market_has_no_swings(self):
        candles = [bar(i, "100", "100", "100") for i in range(50)]
        assert len(detect_swings(candles, PCT2)) == 0

    def test_finds_high_after_drop(self):
        candles = ramp(100, 20, 1)  # 100 -> 120
        candles += ramp(120, 10, -1, index_from=20)  # 120 -> 110 (8% 하락)
        series = detect_swings(candles, PCT2)
        high = series.last(SwingKind.HIGH)
        assert high is not None
        assert high.price == D(120)

    def test_finds_low_after_rise(self):
        candles = ramp(120, 20, -1)  # 120 -> 100
        candles += ramp(100, 10, 1, index_from=20)  # 100 -> 110
        series = detect_swings(candles, PCT2)
        low = series.last(SwingKind.LOW)
        assert low is not None
        assert low.price == D(100)

    def test_alternates_high_low(self):
        candles = ramp(100, 20, 1)
        candles += ramp(120, 20, -1, index_from=20)
        candles += ramp(100, 20, 1, index_from=40)
        candles += ramp(120, 20, -1, index_from=60)
        kinds = [s.kind for s in detect_swings(candles, PCT2).swings]
        for a, b in pairwise(kinds):
            assert a is not b, f"같은 종류가 연속: {kinds}"

    def test_small_wiggles_below_threshold_are_ignored(self):
        """임계 미만의 흔들림은 스윙이 아니다."""
        candles: list[Candle] = []
        for i in range(60):
            base = 1000 + (i % 2)  # 0.1% 진폭
            candles.append(bar(i, str(base), str(base + 1), str(base)))
        assert len(detect_swings(candles, PCT2)) == 0

    def test_threshold_must_be_fraction(self):
        for bad in (D(0), D(-1), D(1), D(2)):
            with pytest.raises(ValueError, match="threshold_pct"):
                detect_swings([bar(0, "100", "101")], bad)

    def test_all_swings_sorted_by_confirmation(self):
        candles = ramp(100, 20, 1) + ramp(120, 20, -1, index_from=20)
        candles += ramp(100, 20, 1, index_from=40)
        confirmations = [s.confirmed_at_ms for s in detect_swings(candles, PCT2).swings]
        assert confirmations == sorted(confirmations)


# --------------------------------------------------------------- 조회


class TestSwingSeries:
    def _series(self) -> SwingSeries:
        candles = ramp(100, 20, 1)
        candles += ramp(120, 20, -1, index_from=20)
        candles += ramp(100, 25, 1, index_from=40)
        candles += ramp(125, 20, -1, index_from=65)
        return detect_swings(candles, PCT2)

    def test_confirmed_as_of_filters(self):
        series = self._series()
        assert len(series) > 0
        earliest = series.swings[0].confirmed_at_ms
        assert len(series.confirmed_as_of(earliest - 1)) == 0
        assert len(series.confirmed_as_of(earliest)) == 1

    def test_confirmed_as_of_preserves_threshold(self):
        series = self._series()
        assert series.confirmed_as_of(10**15).threshold_pct == series.threshold_pct

    def test_last_without_kind(self):
        series = self._series()
        assert series.last() is series.swings[-1]

    def test_last_returns_none_when_empty(self):
        empty = SwingSeries(swings=(), threshold_pct=PCT2)
        assert empty.last() is None
        assert empty.last(SwingKind.HIGH) is None

    def test_of_kind(self):
        series = self._series()
        assert all(s.kind is SwingKind.HIGH for s in series.of_kind(SwingKind.HIGH))

    def test_swing_kind_opposite(self):
        assert SwingKind.HIGH.opposite is SwingKind.LOW
        assert SwingKind.LOW.opposite is SwingKind.HIGH


class TestStructureLabels:
    def test_uptrend_gives_hh_hl(self):
        candles = ramp(100, 20, 1)  # -> 120
        candles += ramp(120, 12, -1, index_from=20)  # -> 108
        candles += ramp(108, 25, 1, index_from=32)  # -> 133 (더 높은 고점)
        candles += ramp(133, 12, -1, index_from=57)  # -> 121 (더 높은 저점)
        candles += ramp(121, 20, 1, index_from=69)
        labels = detect_swings(candles, PCT2).labels()
        assert StructureLabel.HIGHER_HIGH in labels
        assert StructureLabel.HIGHER_LOW in labels

    def test_downtrend_gives_lh_ll(self):
        candles = ramp(130, 20, -1)  # -> 110
        candles += ramp(110, 12, 1, index_from=20)  # -> 122
        candles += ramp(122, 25, -1, index_from=32)  # -> 97 (더 낮은 저점)
        candles += ramp(97, 12, 1, index_from=57)  # -> 109 (더 낮은 고점)
        candles += ramp(109, 20, -1, index_from=69)
        labels = detect_swings(candles, PCT2).labels()
        assert StructureLabel.LOWER_LOW in labels
        assert StructureLabel.LOWER_HIGH in labels

    def test_insufficient_swings_gives_no_labels(self):
        candles = ramp(100, 20, 1) + ramp(120, 10, -1, index_from=20)
        assert detect_swings(candles, PCT2).labels() == ()


class TestFibRetracement:
    def _up_leg(self) -> SwingSeries:
        """저점 100 -> 고점 120 이 모두 확정된 시계열."""
        candles = ramp(120, 20, -1)  # 120 -> 100
        candles += ramp(100, 22, 1, index_from=20)  # 100 -> 122
        candles += ramp(122, 10, -1, index_from=42)  # 되돌림으로 고점 확정
        return detect_swings(candles, PCT2)

    def test_at_high_is_zero(self):
        series = self._up_leg()
        high = series.last(SwingKind.HIGH)
        assert high is not None
        assert series.fib_retracement(high.price) == pytest.approx(0.0, abs=1e-9)

    def test_at_low_is_one(self):
        series = self._up_leg()
        low = series.last(SwingKind.LOW)
        assert low is not None
        assert series.fib_retracement(low.price) == pytest.approx(1.0, abs=1e-9)

    def test_midpoint_is_half(self):
        series = self._up_leg()
        high = series.last(SwingKind.HIGH)
        low = series.last(SwingKind.LOW)
        assert high is not None
        assert low is not None
        mid = (high.price + low.price) / 2
        assert series.fib_retracement(mid) == pytest.approx(0.5, abs=1e-9)

    def test_none_without_both_swings(self):
        rising_only = detect_swings(ramp(100, 30, 1), PCT2)
        assert rising_only.fib_retracement(D(110)) is None

    def test_none_on_empty(self):
        assert SwingSeries(swings=(), threshold_pct=PCT2).fib_retracement(D(100)) is None


# --------------------------------------------------------------- 결정론


def test_repeated_detection_identical():
    candles = ramp(100, 30, 1) + ramp(130, 30, -1, index_from=30)
    first = detect_swings(candles, PCT2)
    for _ in range(10):
        assert detect_swings(candles, PCT2) == first
