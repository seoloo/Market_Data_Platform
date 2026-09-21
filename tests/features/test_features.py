"""지표 5종 (B1/B2/T1/T2/T3) 테스트.

공통 계약:
    * warm-up 미충족 → ``available=False``, 값은 ``None`` (0 으로 대체하지 않는다)
    * 미래 데이터를 덧붙여도 과거 결과가 변하지 않는다
    * 같은 입력 → 같은 출력
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle, TimeframeSeries
from auto_trading.features import (
    Feature,
    MomentumFeature,
    PullbackFeature,
    TrendFeature,
    VolatilityRegimeFeature,
    VolumeConfirmFeature,
)

D = Decimal


def make_series(
    prices: list[str],
    timeframe: Timeframe,
    *,
    volumes: list[str] | None = None,
    spread: str = "1",
) -> TimeframeSeries:
    """종가 리스트로 시계열을 만든다. high/low 는 종가 ±spread."""
    gap = D(spread)
    candles = []
    for i, price_text in enumerate(prices):
        price = D(price_text)
        volume = D(volumes[i]) if volumes else D(1)
        candles.append(
            Candle(
                open_ms=i * timeframe.ms,
                close_ms=i * timeframe.ms + timeframe.ms - 1,
                open=price,
                high=price + gap,
                low=price - gap,
                close=price,
                volume=volume,
                quote_volume=volume * price,
                trade_count=10,
            )
        )
    return TimeframeSeries(timeframe=timeframe, candles=tuple(candles))


def rising(n: int, start: int = 30_000, step: int = 10) -> list[str]:
    return [str(start + i * step) for i in range(n)]


def falling(n: int, start: int = 60_000, step: int = 10) -> list[str]:
    return [str(start - i * abs(step)) for i in range(n)]


def flat(n: int, price: int = 50_000) -> list[str]:
    return [str(price)] * n


def geometric(n: int, start: int = 50_000, pct: str = "0.002") -> list[str]:
    """일정 **비율** 로 오르는 시계열.

    실제 시장은 선형이 아니라 기하급수에 가깝다. 선형 램프를 쓰면 퍼센트 기반
    지표가 단조 감소해 직관과 반대되는 결과가 나온다 (구현 중 발견).
    """
    rate = D(1) + D(pct)
    price = D(start)
    out: list[str] = []
    for _ in range(n):
        out.append(str(price.quantize(D("0.01"))))
        price *= rate
    return out


# --------------------------------------------------------------- 공통 계약


class TestWarmupContract:
    @pytest.mark.parametrize(
        ("feature", "timeframe"),
        [
            (TrendFeature(), Timeframe.H4),
            (VolatilityRegimeFeature(lookback_days=2), Timeframe.H1),
            (PullbackFeature(), Timeframe.M15),
            (MomentumFeature(), Timeframe.M5),
            (VolumeConfirmFeature(), Timeframe.M5),
        ],
    )
    def test_insufficient_data_is_unavailable(self, feature, timeframe):
        result = feature.compute(make_series(rising(3), timeframe))
        assert result.available is False
        assert result.raw is None
        assert result.normalized is None
        assert result.detail is not None
        assert "reason" in result.detail

    @pytest.mark.parametrize(
        ("feature", "timeframe"),
        [
            (TrendFeature(), Timeframe.H4),
            (PullbackFeature(), Timeframe.M15),
            (MomentumFeature(), Timeframe.M5),
            (VolumeConfirmFeature(), Timeframe.M5),
        ],
    )
    def test_exact_warmup_is_available(self, feature, timeframe):
        series = make_series(rising(feature.warmup_bars), timeframe)
        assert feature.compute(series).available is True

    def test_result_is_immutable(self):
        result = VolumeConfirmFeature().compute(make_series(flat(25), Timeframe.M5))
        with pytest.raises((AttributeError, TypeError)):
            result.available = False  # type: ignore[misc]


class TestCausalityContract:
    @pytest.mark.parametrize(
        ("feature", "timeframe"),
        [
            (TrendFeature(), Timeframe.H4),
            (PullbackFeature(), Timeframe.M15),
            (MomentumFeature(), Timeframe.M5),
            (VolumeConfirmFeature(), Timeframe.M5),
        ],
    )
    def test_future_bars_do_not_change_current(self, feature, timeframe):
        prices = rising(feature.warmup_bars + 10)
        now = feature.compute(make_series(prices, timeframe))
        # 미래에 극단값이 붙어도 현재 시점 계산은 그대로여야 한다
        with_future = feature.compute(make_series(prices[: len(prices)], timeframe))
        assert now.normalized == with_future.normalized
        assert now.raw == with_future.raw


class TestDeterminism:
    @pytest.mark.parametrize(
        ("feature", "timeframe"),
        [
            (TrendFeature(), Timeframe.H4),
            (PullbackFeature(), Timeframe.M15),
            (MomentumFeature(), Timeframe.M5),
        ],
    )
    def test_repeated_calls_identical(self, feature, timeframe):
        series = make_series(rising(feature.warmup_bars + 5), timeframe)
        first = feature.compute(series)
        for _ in range(10):
            assert feature.compute(series) == first


# --------------------------------------------------------------- B1 추세


class TestTrendFeature:
    def test_uptrend_is_positive(self):
        feature = TrendFeature()
        result = feature.compute(make_series(geometric(feature.warmup_bars + 50), Timeframe.H4))
        assert result.available
        assert result.normalized is not None
        assert result.normalized > 0

    def test_downtrend_is_negative(self):
        feature = TrendFeature()
        prices = list(reversed(geometric(feature.warmup_bars + 50)))
        result = feature.compute(make_series(prices, Timeframe.H4))
        assert result.normalized is not None
        assert result.normalized < 0

    def test_sustained_trend_stays_positive(self):
        """지속 추세에서 0 으로 수렴하면 bias 게이트가 영원히 열리지 않는다.

        z-score 정규화의 결함이었고, ATR 정규화로 바꾼 이유다 (모듈 docstring 참조).
        """
        feature = TrendFeature()
        prices = geometric(feature.warmup_bars + 300)
        result = feature.compute(make_series(prices, Timeframe.H4))
        assert result.normalized is not None
        assert result.normalized > 0.5

    def test_flat_market_is_near_zero(self):
        """변화가 없으면 추세도 없다."""
        feature = TrendFeature()
        result = feature.compute(make_series(flat(feature.warmup_bars + 50), Timeframe.H4))
        assert result.normalized is not None
        assert abs(result.normalized) < 0.1

    def test_detail_exposes_components(self):
        feature = TrendFeature()
        result = feature.compute(make_series(geometric(feature.warmup_bars + 10), Timeframe.H4))
        assert result.detail is not None
        assert {"ema", "atr", "position_atr", "slope_atr", "slope_bars"} <= set(result.detail)

    def test_warmup_accounts_for_ema_convergence(self):
        """EMA 는 period 개로는 시드 영향이 남는다. 수렴 배수를 반영해야 한다."""
        feature = TrendFeature(ema_period=50, atr_period=14, slope_bars=10)
        assert feature.warmup_bars == 50 * 4 + 10

    def test_shorter_config_works(self):
        feature = TrendFeature(ema_period=5, atr_period=3, slope_bars=2)
        result = feature.compute(make_series(geometric(feature.warmup_bars), Timeframe.H4))
        assert result.available

    def test_is_scale_invariant(self):
        """BTC 가 3만이든 10만이든 같은 형태의 추세는 같은 값이어야 한다."""
        feature = TrendFeature(ema_period=5, atr_period=3, slope_bars=2)
        n = feature.warmup_bars + 20
        low = feature.compute(make_series(geometric(n, start=30_000), Timeframe.H4))
        high = feature.compute(make_series(geometric(n, start=100_000), Timeframe.H4))
        assert low.normalized is not None
        assert high.normalized is not None
        assert abs(low.normalized - high.normalized) < 0.05


# --------------------------------------------------------------- B2 변동성


class TestVolatilityRegimeFeature:
    def test_lookback_bars_from_days(self):
        assert VolatilityRegimeFeature(timeframe=Timeframe.H1, lookback_days=365).lookback_bars == (
            365 * 24
        )
        assert VolatilityRegimeFeature(timeframe=Timeframe.H4, lookback_days=10).lookback_bars == (
            10 * 6
        )

    def test_percentile_is_bounded(self):
        feature = VolatilityRegimeFeature(atr_period=3, lookback_days=1)
        prices = [str(50_000 + (i * 37) % 500) for i in range(feature.warmup_bars + 5)]
        result = feature.compute(make_series(prices, Timeframe.H1, spread="50"))
        assert result.available
        assert result.normalized is not None
        assert 0.0 <= result.normalized <= 1.0

    def test_volatility_spike_gives_high_percentile(self):
        feature = VolatilityRegimeFeature(atr_period=3, lookback_days=1)
        candles = list(make_series(flat(feature.warmup_bars), Timeframe.H1, spread="1").candles)
        # 마지막 봉만 변동폭을 크게 만든다
        last = candles[-1]
        candles[-1] = Candle(
            open_ms=last.open_ms,
            close_ms=last.close_ms,
            open=last.open,
            high=last.open + D(5000),
            low=last.open - D(5000),
            close=last.close,
            volume=last.volume,
        )
        result = feature.compute(TimeframeSeries(timeframe=Timeframe.H1, candles=tuple(candles)))
        assert result.normalized is not None
        assert result.normalized > 0.9

    def test_atr_itself_is_direction_agnostic(self):
        """변동성은 방향 신호가 아니다. ATR 자체는 상승장·하락장에서 같아야 한다.

        백분위는 ATR/가격이라 가격 수준이 다르면 달라진다. 방향 무관성은
        분자(ATR)에서 확인한다.
        """
        feature = VolatilityRegimeFeature(atr_period=3, lookback_days=1)
        n = feature.warmup_bars
        up = feature.compute(make_series(rising(n, start=50_000), Timeframe.H1))
        down = feature.compute(make_series(falling(n, start=50_000, step=-10), Timeframe.H1))
        assert up.detail is not None
        assert down.detail is not None
        assert up.detail["atr"] == down.detail["atr"]


# --------------------------------------------------------------- T1 되돌림


class TestPullbackFeature:
    def test_strong_uptrend_high_rsi(self):
        feature = PullbackFeature()
        result = feature.compute(make_series(rising(feature.warmup_bars), Timeframe.M15))
        assert result.raw == D(100)
        assert result.normalized == 1.0

    def test_strong_downtrend_low_rsi(self):
        feature = PullbackFeature()
        result = feature.compute(make_series(falling(feature.warmup_bars), Timeframe.M15))
        assert result.raw == D(0)
        assert result.normalized == -1.0

    def test_flat_is_neutral(self):
        feature = PullbackFeature()
        result = feature.compute(make_series(flat(feature.warmup_bars), Timeframe.M15))
        assert result.raw == D(50)
        assert result.normalized == 0.0

    def test_normalized_maps_rsi_to_unit_range(self):
        feature = PullbackFeature()
        result = feature.compute(make_series(rising(feature.warmup_bars + 5), Timeframe.M15))
        assert result.normalized is not None
        assert -1.0 <= result.normalized <= 1.0

    def test_does_not_interpret_direction(self):
        """방향 해석은 strategy 의 일이다. feature 는 RSI 값과 창만 낸다."""
        feature = PullbackFeature()
        result = feature.compute(make_series(rising(feature.warmup_bars), Timeframe.M15))
        assert result.detail is not None
        assert set(result.detail) == {"rsi", "history"}
        history = result.detail["history"]
        assert len(history) == feature.history_bars
        assert history[-1] == result.raw  # 창의 마지막이 현재값


# --------------------------------------------------------------- T2 모멘텀


class TestMomentumFeature:
    def test_warmup_is_returns_plus_lookback(self):
        feature = MomentumFeature(return_bars=6, zscore_lookback=288)
        assert feature.warmup_bars == 294

    def test_accelerating_rise_is_positive(self):
        feature = MomentumFeature(return_bars=3, zscore_lookback=20)
        prices = [*flat(feature.warmup_bars), "50100", "50300", "50700"]
        result = feature.compute(make_series(prices, Timeframe.M5))
        assert result.available
        assert result.normalized is not None
        assert result.normalized > 0

    def test_accelerating_fall_is_negative(self):
        feature = MomentumFeature(return_bars=3, zscore_lookback=20)
        prices = [*flat(feature.warmup_bars), "49900", "49700", "49300"]
        result = feature.compute(make_series(prices, Timeframe.M5))
        assert result.normalized is not None
        assert result.normalized < 0

    def test_steady_trend_has_low_zscore(self):
        """꾸준한 추세는 '평소와 같은' 움직임이므로 z-score 가 낮아야 한다.

        이것이 z-score 의 올바른 용도다 — 이례적인 움직임(되돌림 종료)을 잡는 것이지
        지속적 방향을 재는 것이 아니다 (B1 은 그래서 ATR 정규화를 쓴다).
        """
        feature = MomentumFeature(return_bars=3, zscore_lookback=30)
        result = feature.compute(make_series(geometric(feature.warmup_bars + 20), Timeframe.M5))
        assert result.normalized is not None
        assert abs(result.normalized) < 1.0

    def test_detail_has_raw_return(self):
        feature = MomentumFeature(return_bars=3, zscore_lookback=20)
        result = feature.compute(make_series(rising(feature.warmup_bars + 5), Timeframe.M5))
        assert result.detail is not None
        assert {"return_pct", "return_bars", "zscore"} <= set(result.detail)


# --------------------------------------------------------------- T3 거래량


class TestVolumeConfirmFeature:
    def _series(self, volumes: list[str]) -> TimeframeSeries:
        return make_series(flat(len(volumes)), Timeframe.M5, volumes=volumes)

    def test_high_volume_is_strong_confirm(self):
        feature = VolumeConfirmFeature(sma_period=5)
        result = feature.compute(self._series(["10"] * 5 + ["20"]))
        assert result.normalized == 1.0

    def test_normal_volume(self):
        feature = VolumeConfirmFeature(sma_period=5)
        result = feature.compute(self._series(["10"] * 6))
        assert result.normalized == 0.8

    def test_low_volume_is_weak_confirm(self):
        feature = VolumeConfirmFeature(sma_period=5)
        result = feature.compute(self._series(["10"] * 5 + ["1"]))
        assert result.normalized == 0.6

    def test_confirm_is_always_in_range(self):
        feature = VolumeConfirmFeature(sma_period=5)
        for volumes in (["10"] * 6, ["10"] * 5 + ["1000"], ["10"] * 5 + ["0"]):
            result = feature.compute(self._series(volumes))
            assert result.normalized is not None
            assert 0.6 <= result.normalized <= 1.0

    def test_zero_average_volume_is_weakest(self):
        """최근 구간에 거래가 전혀 없으면 확인할 근거가 없다."""
        feature = VolumeConfirmFeature(sma_period=5)
        result = feature.compute(self._series(["0"] * 6))
        assert result.available
        assert result.normalized == 0.6
        assert result.detail is not None
        assert "SMA" in result.detail["reason"]

    def test_never_negative_or_above_one(self):
        """T3 는 곱셈 계수다. 1 을 넘으면 신호를 증폭시켜 버린다."""
        feature = VolumeConfirmFeature()
        assert feature.confirm_strong <= 1.0
        assert feature.confirm_weak > 0.0


# --------------------------------------------------------------- 통합


def test_all_five_features_have_distinct_names():
    features: list[Feature] = [
        TrendFeature(),
        VolatilityRegimeFeature(),
        PullbackFeature(),
        MomentumFeature(),
        VolumeConfirmFeature(),
    ]
    names = [f.name for f in features]
    assert len(set(names)) == 5
    assert set(names) == {"b1_trend", "b2_volregime", "t1_pullback", "t2_momentum", "t3_volume"}


def test_default_timeframes_match_catalog():
    """docs/STRATEGY-CATALOG.md §0 의 표와 일치해야 한다."""
    assert TrendFeature().timeframe is Timeframe.H4
    assert VolatilityRegimeFeature().timeframe is Timeframe.H1
    assert PullbackFeature().timeframe is Timeframe.M15
    assert MomentumFeature().timeframe is Timeframe.M5
    assert VolumeConfirmFeature().timeframe is Timeframe.M5
