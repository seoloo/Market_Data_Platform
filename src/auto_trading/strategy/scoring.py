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
    setup_lookback_bars: int = 12,
    data_quality: float = 1.0,
    regime_stability: float = 1.0,
    spread_risk: float = 0.0,
    event_proximity: float = 0.0,
    funding_extremity: float = 0.0,
    volatility_percentile: float = 0.0,
) -> ScoreSet:
    """상위 추세와 같은 방향의 trigger만 통과시킨다.

    **국면 계수를 여기서 곱하지 않는다** (D14). 변동성 국면은 ``bias.risk_scale`` 로
    사이징에만 반영되고, 극단 국면은 리스크 게이트가 따로 막는다. 방향 점수에 곱하면
    완충장치가 진입 거부권으로 변한다.
    """
    if not bias.available or not trigger.available:
        return ScoreSet.neutral()

    trigger_sign = (trigger.value > 0.0) - (trigger.value < 0.0)
    if bias.direction == 0:
        direction = trigger.value * 0.5 if policy.allow_counter_bias_when_neutral else 0.0
    elif trigger_sign == bias.direction:
        direction = trigger.value
    else:
        direction = 0.0

    # D14 이후 방향 컴포넌트는 T2 하나뿐이라 부호 분산은 항상 0 이다. 그 자리에
    # setup 신선도를 쓴다 — 되돌림 직후일수록 "되돌림 후 재개" 라는 인과가 강하다.
    agreement = trigger.setup.freshness(setup_lookback_bars)
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
        bias_multiplier=float(bias.risk_scale),
        agreement=agreement,
        regime=bias.regime,
    )


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))
