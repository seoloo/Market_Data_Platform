"""빠른 Trigger 층 해석.

T1 되돌림과 T2 모멘텀을 먼저 합한 뒤 한 번만 압축한다. T2를 먼저 압축하고
합성값을 다시 압축하면 진입 70점에 도달할 수 없으므로 이중 압축을 금지한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from auto_trading.config.schema import FeatureConfig
from auto_trading.features.base import FeatureResult
from auto_trading.features.normalize import squash

__all__ = ["TriggerResult", "compute_trigger"]


@dataclass(frozen=True, slots=True)
class TriggerResult:
    value: float
    pullback_signal: float
    momentum_value: float
    volume_confirm: float
    available: bool

    def __post_init__(self) -> None:
        if not -1.0 <= self.value <= 1.0:
            raise ValueError(f"value 는 [-1,1] 이어야 한다: {self.value}")
        if not -1.0 <= self.pullback_signal <= 1.0:
            raise ValueError(f"pullback_signal 은 [-1,1] 이어야 한다: {self.pullback_signal}")
        if not 0.0 <= self.volume_confirm <= 1.0:
            raise ValueError(f"volume_confirm 은 [0,1] 이어야 한다: {self.volume_confirm}")


def compute_trigger(
    pullback: FeatureResult,
    momentum: FeatureResult,
    volume: FeatureResult,
    *,
    bias_direction: int,
    pullback_cfg: FeatureConfig,
    momentum_cfg: FeatureConfig,
) -> TriggerResult:
    """T1/T2 등가중 합성 후 단일 squash와 T3 확인 계수를 적용한다."""
    if (
        not pullback.available
        or pullback.raw is None
        or not momentum.available
        or momentum.normalized is None
        or not volume.available
        or volume.normalized is None
    ):
        return TriggerResult(0.0, 0.0, 0.0, 0.0, available=False)

    t1_signal = _pullback_signal(float(pullback.raw), bias_direction, pullback_cfg)
    min_abs_z = _float_param(momentum_cfg, "min_abs_z", 0.3)
    t2_value = momentum.normalized if abs(momentum.normalized) >= min_abs_z else 0.0
    total_weight = pullback_cfg.weight + momentum_cfg.weight
    if total_weight <= 0.0:
        return TriggerResult(0.0, t1_signal, t2_value, volume.normalized, available=False)

    combined = (pullback_cfg.weight * t1_signal + momentum_cfg.weight * t2_value) / total_weight
    squash_k = _float_param(momentum_cfg, "squash_k", 1.5)
    value = squash(combined, squash_k) * volume.normalized
    return TriggerResult(
        value=value,
        pullback_signal=t1_signal,
        momentum_value=t2_value,
        volume_confirm=volume.normalized,
        available=True,
    )


def _pullback_signal(rsi: float, bias_direction: int, cfg: FeatureConfig) -> float:
    if bias_direction == 0:
        return 0.0

    long_zone = _zone_param(cfg, "long_zone", (35.0, 50.0))
    short_zone = _zone_param(cfg, "short_zone", (50.0, 65.0))
    invalidate_below = _float_param(cfg, "invalidate_below", 30.0)
    invalidate_above = _float_param(cfg, "invalidate_above", 70.0)
    if rsi < invalidate_below or rsi > invalidate_above:
        return 0.0

    if bias_direction > 0:
        lower, upper = long_zone
        if lower <= rsi <= upper:
            return (upper - rsi) / (upper - lower)
        if upper < rsi <= short_zone[1]:
            return 0.2
        return 0.0

    lower, upper = short_zone
    if lower <= rsi <= upper:
        return -(rsi - lower) / (upper - lower)
    if long_zone[0] <= rsi < lower:
        return -0.2
    return 0.0


def _float_param(cfg: FeatureConfig, name: str, default: float) -> float:
    value = cfg.params.get(name, default)
    if isinstance(value, list):
        raise ValueError(f"{name} 은 숫자여야 한다")
    return float(value)


def _zone_param(cfg: FeatureConfig, name: str, default: tuple[float, float]) -> tuple[float, float]:
    value = cfg.params.get(name)
    if value is None:
        return default
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} 은 숫자 2개의 배열이어야 한다")
    return float(value[0]), float(value[1])
