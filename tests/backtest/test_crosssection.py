"""교차 심볼 롱숏 평가 테스트.

H3 의 판정이 여기서 나오므로, **방향을 미리 정하지 않는다**와 **중첩 제거**가
정확해야 한다. 여기가 틀리면 없는 우위를 있다고 보고하게 된다.
"""

from __future__ import annotations

import math

import pytest

from auto_trading.backtest.crosssection import (
    CrossSectionResult,
    PanelObservation,
    evaluate_panel,
    long_short_return,
    preregistered_signal_verdict,
    quantile_returns,
)

HOUR = 3_600_000


def panel(at_ms: int, pairs: dict[str, tuple[float, float]]) -> PanelObservation:
    """``{심볼: (신호값, 선행수익bps)}`` 로 단면 하나를 만든다."""
    return PanelObservation(
        at_ms=at_ms,
        values={s: v for s, (v, _) in pairs.items()},
        forward_bps={s: r for s, (_, r) in pairs.items()},
    )


def ladder(at_ms: int, count: int = 10, *, slope: float = 10.0) -> PanelObservation:
    """신호가 클수록 수익도 큰 단면. 완벽한 신호를 흉내 낸다."""
    return panel(
        at_ms,
        {f"S{i:02d}": (float(i), slope * (i - (count - 1) / 2)) for i in range(count)},
    )


class TestPanelObservation:
    def test_empty_panel_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="비어"):
            PanelObservation(at_ms=1, values={}, forward_bps={})

    def test_missing_forward_return_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="선행수익"):
            PanelObservation(at_ms=1, values={"A": 1.0, "B": 2.0}, forward_bps={"A": 5.0})

    def test_ties_break_by_symbol_so_ranking_is_deterministic(self) -> None:
        observation = panel(1, {"B": (1.0, 5.0), "A": (1.0, -5.0), "C": (1.0, 0.0)})
        assert [s for s, _ in observation.ranked()] == ["A", "B", "C"]


class TestLongShort:
    def test_perfect_signal_gives_positive_spread(self) -> None:
        # 수익 = 10*(i-4.5) → 하위 3개 평균 -35, 상위 3개 평균 +35 → (35-(-35))/2
        assert long_short_return(ladder(0), leg=3) == pytest.approx(35.0)

    def test_inverted_signal_gives_negative_spread(self) -> None:
        """부호를 미리 정하지 않으므로 반대 신호는 음수로 나와야 한다."""
        inverted = panel(0, {f"S{i:02d}": (float(i), -10.0 * i) for i in range(10)})
        spread = long_short_return(inverted, leg=3)
        assert spread is not None
        assert spread < 0

    def test_capital_normalisation_halves_the_raw_spread(self) -> None:
        """자본의 절반씩 양쪽에 담으므로 2 로 나눈다."""
        observation = panel(
            0, {"A": (1.0, -100.0), "B": (2.0, 0.0), "C": (3.0, 0.0), "D": (4.0, 100.0)}
        )
        assert long_short_return(observation, leg=1) == pytest.approx(100.0)

    def test_too_few_symbols_returns_none(self) -> None:
        assert long_short_return(ladder(0, count=4), leg=3) is None


class TestQuantiles:
    def test_monotone_signal_produces_rising_quantiles(self) -> None:
        values = quantile_returns(ladder(0, count=10), buckets=5)
        assert values is not None
        assert list(values) == sorted(values)

    def test_too_few_symbols_returns_none(self) -> None:
        assert quantile_returns(ladder(0, count=6), buckets=5) is None


class TestEvaluatePanel:
    def _series(self, count: int, *, spacing_ms: int, slope: float = 10.0):
        return [ladder(i * spacing_ms, slope=slope) for i in range(count)]

    def test_embargo_drops_overlapping_observations(self) -> None:
        """5분 간격 단면을 2시간 embargo 로 평가하면 2시간에 하나만 남는다."""
        observations = self._series(288, spacing_ms=5 * 60_000)
        result = evaluate_panel("f", "2h", observations, embargo_ms=2 * HOUR)
        assert result.sample_count == 12

    def test_shorter_embargo_keeps_more_samples(self) -> None:
        observations = self._series(288, spacing_ms=5 * 60_000)
        short = evaluate_panel("f", "1h", observations, embargo_ms=HOUR)
        long_ = evaluate_panel("f", "6h", observations, embargo_ms=6 * HOUR)
        assert short.sample_count > long_.sample_count

    def test_zero_embargo_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="embargo_ms"):
            evaluate_panel("f", "2h", self._series(3, spacing_ms=HOUR), embargo_ms=0)

    def test_single_observation_cannot_pass(self) -> None:
        result = evaluate_panel("f", "2h", [ladder(0)], embargo_ms=HOUR)
        assert result.stderr_bps == math.inf
        assert not result.passes(22.0, 1)


class TestPassCriteria:
    def _result(self, count: int, slope: float):
        observations = [ladder(i * 2 * HOUR, slope=slope) for i in range(count)]
        return evaluate_panel("f", "2h", observations, embargo_ms=2 * HOUR)

    def test_clear_effect_passes(self) -> None:
        assert self._result(400, slope=10.0).passes(22.0, 200)

    def test_effect_below_cost_does_not_pass(self) -> None:
        """비용을 못 넘으면 표본이 아무리 많아도 통과하지 못한다."""
        assert not self._result(400, slope=1.0).passes(22.0, 200)

    def test_small_sample_does_not_pass(self) -> None:
        assert not self._result(50, slope=10.0).passes(22.0, 200)

    def test_negative_spread_still_counts_as_tradeable(self) -> None:
        """롱숏을 뒤집어 잡으면 되므로 크기만 본다."""
        inverted = [
            panel(i * 2 * HOUR, {f"S{j:02d}": (float(j), -30.0 * j) for j in range(10)})
            for i in range(400)
        ]
        result = evaluate_panel("f", "2h", inverted, embargo_ms=2 * HOUR)
        assert result.mean_bps < 0
        assert result.tradeable_edge_bps > 0
        assert result.passes(22.0, 200)


class TestStructureChecks:
    def _from_quantiles(self, forwards: list[float], years: int = 2):
        observations = []
        for year in range(years):
            base = year * 365 * 24 * HOUR
            for i in range(200):
                observations.append(
                    panel(
                        base + i * 2 * HOUR,
                        {f"S{j:02d}": (float(j), forwards[j]) for j in range(10)},
                    )
                )
        return evaluate_panel("f", "2h", observations, embargo_ms=2 * HOUR)

    def test_monotone_shape_is_detected(self) -> None:
        result = self._from_quantiles([float(j) * 10 for j in range(10)])
        assert result.is_monotone

    def test_jagged_shape_is_rejected(self) -> None:
        """극단만 좋고 중간이 뒤죽박죽이면 신호가 아니라 우연이다."""
        # 5분위 평균이 0 → 50 → 0 → 50 → 0 으로 오르내린다.
        result = self._from_quantiles([0, 0, 50, 50, 0, 0, 50, 50, 0, 0])
        assert result.quantile_means_bps == (0, 50, 0, 50, 0)
        assert not result.is_monotone

    def test_same_sign_across_years_is_consistent(self) -> None:
        assert self._from_quantiles([float(j) * 10 for j in range(10)]).is_year_consistent

    def test_single_year_is_not_consistent(self) -> None:
        """한 해만으로는 일관성을 판단할 수 없다."""
        result = self._from_quantiles([float(j) * 10 for j in range(10)], years=1)
        assert not result.is_year_consistent


class TestPreregisteredSignalVerdict:
    @staticmethod
    def _result(
        horizon: str,
        *,
        mean_bps: float = 30.0,
        quantiles: tuple[float, ...] = (-20.0, -10.0, 0.0, 10.0, 20.0),
        yearly: dict[int, float] | None = None,
    ) -> CrossSectionResult:
        return CrossSectionResult(
            name="X",
            horizon=horizon,
            sample_count=300,
            mean_bps=mean_bps,
            stderr_bps=2.0,
            positive_rate=0.6,
            quantile_means_bps=quantiles,
            yearly_means_bps=yearly or {2023: 28.0, 2024: 32.0},
        )

    def test_all_preregistered_conditions_pass_together(self) -> None:
        results = [self._result("2h"), self._result("4h"), self._result("6h", mean_bps=-5.0)]

        verdict = preregistered_signal_verdict(results, required_bps=22.0, min_samples=200)

        assert verdict.passes
        assert verdict.qualifying_horizons == ("2h", "4h")
        assert verdict.failure_reasons == ()

    def test_numeric_edge_alone_does_not_pass_without_monotonicity(self) -> None:
        jagged = (0.0, 20.0, -10.0, 15.0, 5.0)
        results = [
            self._result("2h", quantiles=jagged),
            self._result("4h", mean_bps=10.0, quantiles=jagged),
            self._result("6h", mean_bps=-5.0, quantiles=jagged),
        ]

        verdict = preregistered_signal_verdict(results, required_bps=22.0, min_samples=200)

        assert not verdict.passes
        assert "MONOTONICITY" in verdict.failure_reasons

    def test_one_horizon_cannot_pass_without_a_same_sign_peer(self) -> None:
        results = [
            self._result("2h"),
            self._result("4h", mean_bps=-10.0),
            self._result("6h", mean_bps=0.0),
        ]

        verdict = preregistered_signal_verdict(results, required_bps=22.0, min_samples=200)

        assert not verdict.passes
        assert "HORIZON_SIGN" in verdict.failure_reasons

    def test_train_year_sign_reversal_rejects_numeric_edge(self) -> None:
        inconsistent = {2023: 35.0, 2024: -5.0}
        results = [
            self._result("2h", yearly=inconsistent),
            self._result("4h", mean_bps=10.0),
            self._result("6h", mean_bps=-5.0),
        ]

        verdict = preregistered_signal_verdict(results, required_bps=22.0, min_samples=200)

        assert not verdict.passes
        assert "YEAR_SIGN" in verdict.failure_reasons

    def test_duplicate_horizon_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="중복"):
            preregistered_signal_verdict(
                [self._result("2h"), self._result("2h")],
                required_bps=22.0,
                min_samples=200,
            )
