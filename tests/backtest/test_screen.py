"""사전 등록 선별 도구 테스트.

이 도구가 틀리면 **잘못된 가설을 통과시킨다.** H1 을 기각한 근거가 여기서 나오므로,
중첩 제거와 통과 판정은 특히 단단해야 한다.
"""

from __future__ import annotations

import pytest

from auto_trading.backtest.screen import (
    ScreenOutcome,
    SignalPoint,
    SignalSeries,
    screen_signal,
)

HOUR = 3_600_000
HORIZONS = {"2h": 2 * HOUR, "6h": 6 * HOUR}


def point(index: int, value: float, *, bps: float = 0.0, step_ms: int = 300_000) -> SignalPoint:
    return SignalPoint(
        at_ms=1_609_459_200_000 + index * step_ms,
        value=value,
        forward_bps={"2h": bps, "6h": bps},
    )


class TestSignalPoint:
    def test_empty_forward_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="forward_bps"):
            SignalPoint(at_ms=1, value=0.0, forward_bps={})

    def test_non_finite_value_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="유한"):
            SignalPoint(at_ms=1, value=float("nan"), forward_bps={"2h": 0.0})

    def test_out_of_order_series_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="오름차순"):
            SignalSeries("f", (point(5, 1.0), point(1, 1.0)))


class TestEmbargo:
    def test_overlapping_points_collapse_to_the_horizon_spacing(self) -> None:
        """5분 간격 신호를 2시간 horizon 으로 재면 2시간에 하나만 남아야 한다."""
        series = SignalSeries("f", tuple(point(i, 10.0, bps=50.0) for i in range(288)))
        result = screen_signal(series, horizons_ms={"2h": 2 * HOUR}, decile=0.4)
        high = next(o for o in result.outcomes if o.bucket == "high")
        # 288개 x 5분 = 24시간 → 2시간 embargo 면 12개 남는다.
        assert high.sample_count == 12

    def test_longer_horizon_leaves_fewer_samples(self) -> None:
        series = SignalSeries("f", tuple(point(i, 10.0, bps=50.0) for i in range(288)))
        result = screen_signal(series, horizons_ms=HORIZONS, decile=0.4)
        counts = {o.horizon: o.sample_count for o in result.outcomes if o.bucket == "high"}
        assert counts["2h"] > counts["6h"]

    def test_earliest_point_is_kept_not_the_best(self) -> None:
        """수익률을 보고 고르면 선별 자체가 과최적화가 된다."""
        points = (point(0, 1.0, bps=-100.0), point(1, 1.0, bps=+900.0))
        result = screen_signal(SignalSeries("f", points), horizons_ms={"2h": HOUR}, decile=0.4)
        low = next(o for o in result.outcomes if o.bucket == "low")
        assert low.sample_count == 1
        assert low.mean_bps == pytest.approx(-100.0)


class TestDirectionIsNotAssumed:
    def test_both_tails_are_reported(self) -> None:
        points = tuple(point(i * 24, float(i), bps=+60.0 if i >= 90 else -60.0) for i in range(100))
        result = screen_signal(SignalSeries("f", points), horizons_ms={"2h": HOUR}, decile=0.10)
        buckets = {o.bucket: o for o in result.outcomes}
        assert buckets["high"].mean_bps > 0
        assert buckets["low"].mean_bps < 0

    def test_negative_long_return_still_counts_as_tradeable_edge(self) -> None:
        """롱으로 −40bps 면 숏으로 +40bps 다. 방향을 미리 정하지 않았으므로 크기만 본다."""
        outcome = ScreenOutcome("2h", "low", 300, -40.0, 5.0, 0.2)
        assert outcome.tradeable_edge_bps == pytest.approx(40.0)
        assert outcome.lower_bound_bps == pytest.approx(30.0)
        assert outcome.passes(18.0, 200)


class TestPassCriteria:
    def test_wide_error_bars_do_not_pass(self) -> None:
        """평균이 커도 표준오차가 크면 통과하지 못한다 — H1 에서 배운 것."""
        outcome = ScreenOutcome("2h", "high", 300, 30.0, 12.0, 0.6)
        assert not outcome.passes(18.0, 200)

    def test_small_sample_does_not_pass(self) -> None:
        outcome = ScreenOutcome("2h", "high", 50, 100.0, 1.0, 0.9)
        assert not outcome.passes(18.0, 200)

    def test_clear_effect_passes(self) -> None:
        outcome = ScreenOutcome("2h", "high", 400, 40.0, 3.0, 0.7)
        assert outcome.passes(18.0, 200)


class TestYearConsistency:
    def _series(self, second_year_sign: float) -> SignalSeries:
        points = []
        for year_index, sign in enumerate((1.0, second_year_sign)):
            base = year_index * 365 * 24 * 12
            for i in range(200):
                points.append(point(base + i * 24, 0.0, bps=-50.0 * sign))
                points.append(point(base + i * 24 + 12, 100.0, bps=+50.0 * sign))
        return SignalSeries("f", tuple(sorted(points, key=lambda p: p.at_ms)))

    def test_same_sign_in_both_years_is_consistent(self) -> None:
        result = screen_signal(self._series(1.0), horizons_ms={"2h": HOUR}, decile=0.3)
        assert result.is_year_consistent("2h")

    def test_flipped_sign_is_not_consistent(self) -> None:
        """한 해가 전체를 만들어낸 경우를 걸러야 한다."""
        result = screen_signal(self._series(-1.0), horizons_ms={"2h": HOUR}, decile=0.3)
        assert not result.is_year_consistent("2h")


class TestQuintiles:
    def test_monotone_signal_shows_a_rising_shape(self) -> None:
        points = tuple(point(i * 24, float(i), bps=float(i) - 50.0) for i in range(100))
        result = screen_signal(SignalSeries("f", points), horizons_ms={"2h": HOUR}, decile=0.10)
        quints = result.quintile_means_bps["2h"]
        assert len(quints) == 5
        assert list(quints) == sorted(quints)

    def test_empty_series_returns_nothing(self) -> None:
        result = screen_signal(SignalSeries("f", ()), horizons_ms=HORIZONS)
        assert result.total_points == 0
        assert result.outcomes == ()


def test_invalid_decile_is_rejected() -> None:
    with pytest.raises(ValueError, match="decile"):
        screen_signal(SignalSeries("f", (point(0, 1.0),)), horizons_ms=HORIZONS, decile=0.6)
