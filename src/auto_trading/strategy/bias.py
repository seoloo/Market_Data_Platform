"""느린 Bias 층 해석.

B1은 방향을 허가하고 B2는 변동성 국면에 따라 그 허가 강도를 조절한다.
Feature가 측정만 담당하도록 해석 로직은 이 모듈에 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass

from auto_trading.config.schema import PolicyConfig, VolatilityGateConfig
from auto_trading.features.base import FeatureResult

__all__ = ["BiasResult", "compute_bias"]


@dataclass(frozen=True, slots=True)
class BiasResult:
    direction: int
    multiplier: float
    regime: str
    available: bool

    def __post_init__(self) -> None:
        if self.direction not in (-1, 0, 1):
            raise ValueError(f"direction 은 -1/0/1 이어야 한다: {self.direction}")
        if not 0.0 <= self.multiplier <= 1.0:
            raise ValueError(f"multiplier 는 [0,1] 이어야 한다: {self.multiplier}")


def compute_bias(
    trend: FeatureResult,
    volatility: FeatureResult,
    policy: PolicyConfig,
    volatility_gate: VolatilityGateConfig,
) -> BiasResult:
    """B1 방향과 B2 변동성 국면을 하나의 게이트로 합성한다."""
    if (
        not trend.available
        or trend.normalized is None
        or not volatility.available
        or volatility.normalized is None
    ):
        return BiasResult(direction=0, multiplier=0.0, regime="unavailable", available=False)

    strength = trend.normalized
    direction = 0 if abs(strength) < policy.bias_threshold else 1 if strength > 0.0 else -1

    percentile = volatility.normalized
    lower = volatility_gate.atr_pct_lower
    upper = volatility_gate.atr_pct_upper
    if volatility_gate.enabled and (percentile < lower or percentile > upper):
        return BiasResult(direction=direction, multiplier=0.0, regime="extreme", available=True)
    if percentile > 0.70:
        return BiasResult(direction=direction, multiplier=0.5, regime="high", available=True)
    return BiasResult(direction=direction, multiplier=1.0, regime="normal", available=True)
