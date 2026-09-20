"""정규화 테스트.

**가장 중요한 것은 causality 테스트다.** 미래 값을 덧붙여도 앞 시점 결과가 바뀌지 않아야 한다.
이 성질이 깨지면 백테스트 전체가 무의미해진다 (09-failure-modes.md F7).
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.features.normalize import (
    SIGNAL_DECIMALS,
    causal_mean,
    causal_percentile_rank,
    causal_percentile_rank_series,
    causal_stdev,
    causal_zscore,
    causal_zscore_series,
    clip,
    squash,
    to_floats,
)

D = Decimal


# --------------------------------------------------------------- causality


@pytest.mark.leakage
class TestCausality:
    """미래 데이터가 과거 결과에 영향을 주면 안 된다."""

    def test_zscore_ignores_appended_future(self):
        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        now = causal_zscore(series, lookback=5)
        with_future = causal_zscore([*series, 9999.0, -9999.0][:5], lookback=5)
        assert now == with_future

    def test_zscore_at_index_unchanged_by_later_data(self):
        """시계열 버전: 뒤에 값을 더해도 앞 시점 값이 그대로여야 한다."""
        base = [float(i % 7) for i in range(60)]
        short = causal_zscore_series(base, lookback=10)
        extended = causal_zscore_series([*base, 1e6, -1e6, 5.0], lookback=10)
        assert short == extended[: len(base)]

    def test_percentile_at_index_unchanged_by_later_data(self):
        base = [float((i * 37) % 101) for i in range(80)]
        short = causal_percentile_rank_series(base, lookback=20)
        extended = causal_percentile_rank_series([*base, 1e9, -1e9], lookback=20)
        assert short == extended[: len(base)]

    def test_mean_ignores_appended_future(self):
        series = [1.0, 2.0, 3.0]
        assert causal_mean(series, 3) == causal_mean([*series][:3], 3)

    @given(
        base=st.lists(st.floats(-1000, 1000), min_size=30, max_size=60),
        future=st.lists(st.floats(-1e6, 1e6), min_size=1, max_size=10),
    )
    def test_property_future_never_affects_past(self, base, future):
        lookback = 10
        short = causal_zscore_series(base, lookback)
        extended = causal_zscore_series([*base, *future], lookback)
        assert short == extended[: len(base)]


# --------------------------------------------------------------- warm-up


class TestWarmup:
    """표본이 부족하면 None. 0 을 돌려주면 '정상 신호' 로 오해된다."""

    @pytest.mark.parametrize(
        "func", [causal_mean, causal_stdev, causal_zscore, causal_percentile_rank]
    )
    def test_insufficient_data_returns_none(self, func):
        assert func([1.0, 2.0], lookback=5) is None

    def test_exact_lookback_works(self):
        assert causal_mean([1.0, 2.0, 3.0], lookback=3) == 3.0 / 1.5 / 1  # 2.0

    def test_series_leading_values_are_none(self):
        out = causal_zscore_series([float(i) for i in range(10)], lookback=4)
        assert out[:3] == [None, None, None]
        assert out[3] is not None

    def test_series_shorter_than_lookback_is_all_none(self):
        assert causal_zscore_series([1.0, 2.0], lookback=5) == [None, None]

    def test_empty_input(self):
        assert causal_zscore_series([], lookback=5) == []
        assert causal_mean([], lookback=1) is None


# --------------------------------------------------------------- 정확성


class TestCorrectness:
    def test_mean(self):
        assert causal_mean([1.0, 2.0, 3.0, 4.0], lookback=4) == 2.5

    def test_mean_uses_only_last_lookback(self):
        assert causal_mean([100.0, 1.0, 2.0, 3.0], lookback=3) == 2.0

    def test_stdev_matches_stdlib(self):
        data = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        expected = statistics.stdev(data)
        assert causal_stdev(data, lookback=len(data)) == pytest.approx(expected, abs=1e-9)

    def test_zscore_of_last_element(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        mean = 3.0
        sd = statistics.stdev(data)
        assert causal_zscore(data, 5) == pytest.approx((5.0 - mean) / sd, abs=1e-9)

    def test_zscore_zero_variance(self):
        """모든 값이 같으면 변화가 없다 = 신호가 없다."""
        assert causal_zscore([7.0] * 10, lookback=10) == 0.0

    def test_percentile_rank_max(self):
        assert causal_percentile_rank([1.0, 2.0, 3.0, 10.0], lookback=4) == 0.875

    def test_percentile_rank_min(self):
        assert causal_percentile_rank([5.0, 4.0, 3.0, 1.0], lookback=4) == 0.125

    def test_percentile_rank_all_equal_is_half(self):
        assert causal_percentile_rank([3.0] * 8, lookback=8) == 0.5

    def test_percentile_rank_is_bounded(self):
        data = [float(i) for i in range(100)]
        for i in range(10, 100):
            rank = causal_percentile_rank(data[: i + 1], lookback=10)
            assert rank is not None
            assert 0.0 <= rank <= 1.0


class TestSeriesMatchesPointwise:
    """시계열 최적화 구현이 단순 구현과 같은 값을 내야 한다."""

    @pytest.mark.parametrize("lookback", [2, 5, 20])
    def test_zscore_series_matches(self, lookback):
        data = [float((i * 13) % 29) + 0.5 for i in range(60)]
        series = causal_zscore_series(data, lookback)
        for i in range(lookback - 1, len(data)):
            expected = causal_zscore(data[: i + 1], lookback)
            assert series[i] == pytest.approx(expected, abs=1e-6), f"index {i}"

    @pytest.mark.parametrize("lookback", [1, 4, 25])
    def test_percentile_series_matches(self, lookback):
        data = [float((i * 37) % 101) for i in range(80)]
        series = causal_percentile_rank_series(data, lookback)
        for i in range(lookback - 1, len(data)):
            assert series[i] == causal_percentile_rank(data[: i + 1], lookback), f"index {i}"

    def test_percentile_series_handles_duplicates(self):
        data = [1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 1.0, 2.0]
        series = causal_percentile_rank_series(data, 4)
        for i in range(3, len(data)):
            assert series[i] == causal_percentile_rank(data[: i + 1], 4)


# --------------------------------------------------------------- squash


class TestSquash:
    def test_bounded(self):
        for value in (-1e9, -10.0, 0.0, 10.0, 1e9):
            assert -1.0 <= squash(value) <= 1.0

    def test_zero_maps_to_zero(self):
        assert squash(0.0) == 0.0

    def test_sign_preserved(self):
        assert squash(2.0) > 0
        assert squash(-2.0) < 0

    def test_symmetric(self):
        assert squash(3.0) == -squash(-3.0)

    def test_k_is_the_half_point(self):
        """입력이 k 일 때 출력이 정확히 0.5 여야 한다."""
        assert squash(1.5, k=1.5) == 0.5
        assert squash(2.0, k=2.0) == 0.5

    def test_monotonic(self):
        values = [squash(x / 4) for x in range(-40, 41)]
        assert values == sorted(values)

    def test_outliers_cannot_dominate(self):
        """이상치가 신호를 지배하지 못한다.

        입력이 1조 배가 되어도 출력 차이는 0.02 미만이다.
        (극단값에서는 양자화로 정확히 1.0 이 되며, 경계값이므로 정상이다)
        """
        assert squash(1e12) <= 1.0
        assert squash(1e12) - squash(100.0) < 0.02
        assert squash(1e12) == squash(1e15)  # 더 커져도 달라지지 않는다

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError, match="k"):
            squash(1.0, k=0.0)

    def test_uses_no_transcendental_functions(self):
        """libm 구현 차이로 플랫폼 간 결과가 갈리면 Parity Test 가 무의미해진다.

        대수식이므로 tanh 와 값이 달라야 정상이다.
        """
        assert squash(1.0, k=1.5) != pytest.approx(math.tanh(1.0 / 1.5), abs=1e-6)


# --------------------------------------------------------------- 결정론


class TestDeterminism:
    def test_repeated_calls_identical(self):
        data = [float(i) * 1.7 for i in range(200)]
        first = causal_zscore_series(data, 30)
        for _ in range(20):
            assert causal_zscore_series(data, 30) == first

    def test_output_is_quantized(self):
        """양자화로 플랫폼 간 미세 차이가 하위 계층에 전파되지 않게 한다."""
        value = squash(1.0 / 3.0)
        assert round(value, SIGNAL_DECIMALS) == value

    def test_decimal_and_float_inputs_agree(self):
        floats = [1.5, 2.5, 3.5, 4.5]
        decimals = [D("1.5"), D("2.5"), D("3.5"), D("4.5")]
        assert causal_zscore(floats, 4) == causal_zscore(decimals, 4)
        assert causal_percentile_rank(floats, 4) == causal_percentile_rank(decimals, 4)

    def test_to_floats_accepts_decimal(self):
        assert to_floats([D("1.25"), D("2.5")]) == [1.25, 2.5]


# --------------------------------------------------------------- 입력 검증


class TestValidation:
    @pytest.mark.parametrize("func", [causal_mean, causal_percentile_rank])
    def test_lookback_must_be_positive(self, func):
        with pytest.raises(ValueError, match="lookback"):
            func([1.0, 2.0], lookback=0)

    @pytest.mark.parametrize("func", [causal_stdev, causal_zscore, causal_zscore_series])
    def test_stdev_needs_two_samples(self, func):
        with pytest.raises(ValueError, match="lookback"):
            func([1.0, 2.0], lookback=1)

    def test_clip(self):
        assert clip(5.0, 0.0, 1.0) == 1.0
        assert clip(-5.0, 0.0, 1.0) == 0.0
        assert clip(0.5, 0.0, 1.0) == 0.5

    def test_clip_rejects_inverted_range(self):
        with pytest.raises(ValueError, match="뒤집혔다"):
            clip(0.5, 1.0, 0.0)


# --------------------------------------------------------------- 성능 특성


def test_percentile_series_handles_long_lookback():
    """1년 룩백(8,760)을 단순 구현으로 돌리면 백테스트가 실용적이지 않다."""
    data = [float((i * 7919) % 10007) for i in range(20_000)]
    out = causal_percentile_rank_series(data, 8_760)
    assert out[8_758] is None
    assert out[8_759] is not None
    assert all(0.0 <= v <= 1.0 for v in out[8_759:] if v is not None)
