"""포지션 상태와 방향 점수만으로 진입·신호 청산을 결정하는 순수 정책."""

from __future__ import annotations

from auto_trading.config.schema import PolicyConfig
from auto_trading.core.enums import Action, PositionSide
from auto_trading.core.types import PortfolioState, ScoreSet

__all__ = ["resolve_action"]


def resolve_action(
    scores: ScoreSet,
    portfolio: PortfolioState,
    cfg: PolicyConfig,
) -> Action:
    """진입 임계값과 D12 점수 역전 청산 규칙을 적용한다.

    청산은 안전 방향이므로 보유시간이나 손익에 의존하지 않는다. 정확한 동점에서는
    포지션을 유지하며, 반대 점수가 현재 방향 점수를 엄격히 추월해야 청산한다.
    """
    position = portfolio.position
    if position is not None and not position.is_flat:
        if position.side is PositionSide.LONG:
            return Action.CLOSE if scores.short_score > scores.long_score else Action.NONE
        if position.side is PositionSide.SHORT:
            return Action.CLOSE if scores.long_score > scores.short_score else Action.NONE
        return Action.NONE

    if scores.long_score >= cfg.long_entry_threshold:
        return Action.OPEN_LONG
    if scores.short_score >= cfg.short_entry_threshold:
        return Action.OPEN_SHORT
    return Action.NONE
