"""설정에서 MVP feature 5개를 조립하는 단일 지점.

`decide()` 와 `SnapshotBuilder` 가 **같은** feature 객체 정의를 써야 한다.
따로 만들면 스냅샷이 확보한 봉 수와 feature 가 요구하는 warm-up 이 어긋나,
실거래에서만 `available=False` 가 뜨는 종류의 버그가 생긴다.

이 모듈은 순수하다 — I/O 없음, 입력이 같으면 출력이 같다 (AGENTS.md §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import FeatureConfig, FeaturesConfig
from auto_trading.core.enums import Timeframe
from auto_trading.features.base import Feature
from auto_trading.features.momentum import MomentumFeature, PullbackFeature
from auto_trading.features.trend import TrendFeature
from auto_trading.features.volatility import VolatilityRegimeFeature
from auto_trading.features.volume import VolumeConfirmFeature

__all__ = ["FeatureSet", "build_feature_set", "float_param", "int_param"]


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """MVP feature 5개. 순서는 Bias 2개 → Trigger 3개로 고정한다."""

    trend: TrendFeature
    volatility: VolatilityRegimeFeature
    pullback: PullbackFeature
    momentum: MomentumFeature
    volume: VolumeConfirmFeature

    def as_tuple(self) -> tuple[Feature, ...]:
        return (self.trend, self.volatility, self.pullback, self.momentum, self.volume)

    def warmup_by_timeframe(self) -> dict[Timeframe, int]:
        """타임프레임별로 **가장 깊은** warm-up 요구치.

        같은 TF 를 여러 feature 가 쓰면 (예: 5m 의 T2/T3) 더 깊은 쪽이 기준이다.
        """
        required: dict[Timeframe, int] = {}
        for feature in self.as_tuple():
            tf = feature.timeframe
            required[tf] = max(required.get(tf, 0), feature.warmup_bars)
        return required


def build_feature_set(cfg: FeaturesConfig) -> FeatureSet:
    """설정값으로 feature 5개를 조립한다."""
    b1, b2 = cfg.b1_trend, cfg.b2_volregime
    t1, t2, t3 = cfg.t1_pullback, cfg.t2_momentum, cfg.t3_volume
    return FeatureSet(
        trend=TrendFeature(
            timeframe=b1.timeframe,
            ema_period=int_param(b1, "ema_period", 50),
            atr_period=int_param(b1, "atr_period", 14),
            slope_bars=int_param(b1, "slope_bars", 10),
        ),
        volatility=VolatilityRegimeFeature(
            timeframe=b2.timeframe,
            atr_period=int_param(b2, "atr_period", 14),
            lookback_days=int_param(b2, "lookback_days", 365),
        ),
        pullback=PullbackFeature(
            timeframe=t1.timeframe,
            rsi_period=int_param(t1, "rsi_period", 14),
        ),
        momentum=MomentumFeature(
            timeframe=t2.timeframe,
            return_bars=int_param(t2, "return_bars", 6),
            zscore_lookback=int_param(t2, "zscore_lookback", 288),
        ),
        volume=VolumeConfirmFeature(
            timeframe=t3.timeframe,
            sma_period=int_param(t3, "sma_period", 20),
            strong_ratio=Decimal(str(float_param(t3, "strong_ratio", 1.2))),
            weak_ratio=Decimal(str(float_param(t3, "weak_ratio", 0.8))),
            confirm_strong=float_param(t3, "confirm_strong", 1.0),
            confirm_normal=float_param(t3, "confirm_normal", 0.8),
            confirm_weak=float_param(t3, "confirm_weak", 0.6),
        ),
    )


def int_param(cfg: FeatureConfig, name: str, default: int) -> int:
    value = cfg.params.get(name, default)
    if isinstance(value, list):
        raise ValueError(f"{name} 은 정수여야 한다")
    return int(value)


def float_param(cfg: FeatureConfig, name: str, default: float) -> float:
    value = cfg.params.get(name, default)
    if isinstance(value, list):
        raise ValueError(f"{name} 은 숫자여야 한다")
    return float(value)
