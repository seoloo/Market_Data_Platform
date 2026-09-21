"""빠른 Trigger 층 해석 (D14).

**순서가 있는 구조다.** T1 되돌림이 *먼저* setup 을 걸고, T2 모멘텀이 *그 다음*
방아쇠를 당긴다. T3 는 방향이 없으므로 마지막에 확인 계수로 곱한다.

    setup  = T1 되돌림이 최근 창 안에 있었는가 (strategy/setup.py)
    trigger = squash(T2_z, k) * T3_confirm      — setup 이 걸려 있을 때만

D14 이전에는 ``squash(0.5*T1 + 0.5*T2_z) * T3`` 로 **같은 시점**의 두 값을 더했다.
실측에서 진입 16건 중 12건의 T1 이 0 이었다 — 되돌림(낮은 RSI)과 모멘텀 급등
(높은 z)은 같은 봉에서 동시에 성립할 수 없기 때문이다. 가중합은 가설 H1 이 말한
**시간 순서**를 표현하지 못한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import FeatureConfig
from auto_trading.features.base import FeatureResult
from auto_trading.features.normalize import squash
from auto_trading.features.registry import float_param
from auto_trading.strategy.setup import SetupState, detect_setup

__all__ = ["TriggerResult", "compute_trigger"]


@dataclass(frozen=True, slots=True)
class TriggerResult:
    value: float
    momentum_value: float
    volume_confirm: float
    setup: SetupState
    available: bool

    def __post_init__(self) -> None:
        if not -1.0 <= self.value <= 1.0:
            raise ValueError(f"value 는 [-1,1] 이어야 한다: {self.value}")
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
    """setup 이 걸려 있을 때만 T2 방아쇠를 유효 신호로 삼는다."""
    unavailable = SetupState(False, None, None, "no_history")
    if (
        not pullback.available
        or not momentum.available
        or momentum.normalized is None
        or not volume.available
        or volume.normalized is None
    ):
        return TriggerResult(0.0, 0.0, 0.0, unavailable, available=False)

    history = _rsi_history(pullback)
    if history is None:
        return TriggerResult(0.0, 0.0, 0.0, unavailable, available=False)

    setup = detect_setup(history, bias_direction, pullback_cfg)
    min_abs_z = float_param(momentum_cfg, "min_abs_z", 0.3)
    t2_value = momentum.normalized if abs(momentum.normalized) >= min_abs_z else 0.0

    if not setup.armed:
        # setup 이 없으면 방아쇠는 의미가 없다. 0 으로 두되 available 은 True 다 —
        # 계산에 실패한 것이 아니라 "지금은 진입 조건이 아니다" 이기 때문이다.
        return TriggerResult(0.0, t2_value, volume.normalized, setup, available=True)

    squash_k = float_param(momentum_cfg, "squash_k", 1.5)
    value = squash(t2_value, squash_k) * volume.normalized
    return TriggerResult(
        value=value,
        momentum_value=t2_value,
        volume_confirm=volume.normalized,
        setup=setup,
        available=True,
    )


def _rsi_history(pullback: FeatureResult) -> tuple[Decimal, ...] | None:
    detail = pullback.detail
    if detail is None:
        return None
    history = detail.get("history")
    if not isinstance(history, tuple) or not history:
        return None
    return history
