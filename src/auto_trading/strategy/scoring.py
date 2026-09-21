"""Bias와 Trigger를 방향·신뢰·위험 점수로 합성한다."""

from __future__ import annotations

from auto_trading.config.schema import PolicyConfig
from auto_trading.core.types import ScoreSet
from auto_trading.strategy.bias import BiasResult
from auto_trading.strategy.trigger import TriggerResult

__all__ = ["compose_scores"]


def compose_scores(
    bias: BiasResult,
    trigger: TriggerResult,
    policy: PolicyConfig,
    *,
    data_quality: float = 1.0,
    regime_stability: float = 1.0,
    spread_risk: float = 0.0,
    event_proximity: float = 0.0,
    funding_extremity: float = 0.0,
    volatility_percentile: float = 0.0,
) -> ScoreSet:
    """상위 추세와 같은 방향의 trigger만 통과시킨다."""
    if not bias.available or not trigger.available:
        return ScoreSet.neutral()

    trigger_sign = (trigger.value > 0.0) - (trigger.value < 0.0)
    if bias.direction == 0:
        direction = trigger.value * 0.5 if policy.allow_counter_bias_when_neutral else 0.0
    elif trigger_sign == bias.direction:
        direction = trigger.value * bias.multiplier
    else:
        direction = 0.0

    agreement = _agreement(trigger.pullback_signal, trigger.momentum_value)
    magnitude = min(1.0, abs(direction) / policy.confidence_full_at)
    confidence = 100.0 * (agreement**0.5) * _clip01(data_quality)
    confidence *= _clip01(regime_stability) * magnitude
    risk = 100.0 * _clip01(
        0.5 * _clip01(volatility_percentile)
        + 0.2 * _clip01(spread_risk)
        + 0.2 * _clip01(event_proximity)
        + 0.1 * _clip01(funding_extremity)
    )
    return ScoreSet(
        direction=round(direction, 9),
        confidence_score=round(confidence, 9),
        risk_score=round(risk, 9),
        bias_direction=bias.direction,
        bias_multiplier=bias.multiplier,
        agreement=agreement,
        regime=bias.regime,
    )


def _agreement(first: float, second: float) -> float:
    first_sign = (first > 0.0) - (first < 0.0)
    second_sign = (second > 0.0) - (second < 0.0)
    if first_sign == 0 and second_sign == 0:
        return 0.0
    if first_sign == 0 or second_sign == 0:
        return 0.5
    return 1.0 if first_sign == second_sign else 0.0


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))
