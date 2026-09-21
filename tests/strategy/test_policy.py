"""D12 점수 역전 청산과 진입 임계값 정책 테스트."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.config.schema import PolicyConfig
from auto_trading.core.enums import Action, PositionSide, PositionState
from auto_trading.core.types import PortfolioState, PositionSnapshot, ScoreSet
from auto_trading.strategy.policy import resolve_action

D = Decimal
ZERO = D("0")
CFG = PolicyConfig()


def scores(direction: float) -> ScoreSet:
    return ScoreSet(
        direction=direction,
        confidence_score=50.0,
        risk_score=0.0,
        bias_direction=0,
        bias_multiplier=0.0,
        agreement=0.0,
    )


def portfolio(
    side: PositionSide | None = None,
    *,
    unrealized_pnl: Decimal = ZERO,
    opened_ms: int = 0,
) -> PortfolioState:
    position = None
    if side is not None:
        position = PositionSnapshot(
            symbol="BTCUSDT",
            side=side,
            state=PositionState.OPEN,
            qty=D("0.01"),
            entry_price=D("100000"),
            opened_ms=opened_ms,
            unrealized_pnl=unrealized_pnl,
        )
    return PortfolioState(
        equity=D("1000"),
        wallet_balance=D("1000"),
        available_balance=D("900"),
        equity_high_water=D("1000"),
        position=position,
    )


@pytest.mark.parametrize("unrealized_pnl", [D("10"), D("-10")])
def test_long_closes_when_short_score_strictly_overtakes(
    unrealized_pnl: Decimal,
) -> None:
    assert (
        resolve_action(
            scores(-0.01), portfolio(PositionSide.LONG, unrealized_pnl=unrealized_pnl), CFG
        )
        is Action.CLOSE
    )


@pytest.mark.parametrize("unrealized_pnl", [D("10"), D("-10")])
def test_short_closes_when_long_score_strictly_overtakes(
    unrealized_pnl: Decimal,
) -> None:
    assert (
        resolve_action(
            scores(0.01), portfolio(PositionSide.SHORT, unrealized_pnl=unrealized_pnl), CFG
        )
        is Action.CLOSE
    )


@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_exact_score_tie_holds(side: PositionSide) -> None:
    assert resolve_action(scores(0.0), portfolio(side), CFG) is Action.NONE


def test_holding_duration_does_not_force_exit() -> None:
    ten_years_ago_ms = -(10 * 365 * 24 * 60 * 60 * 1000)
    assert (
        resolve_action(scores(0.01), portfolio(PositionSide.LONG, opened_ms=ten_years_ago_ms), CFG)
        is Action.NONE
    )


@pytest.mark.parametrize(
    ("direction", "expected"),
    [(0.4, Action.OPEN_LONG), (-0.4, Action.OPEN_SHORT), (0.0, Action.NONE)],
)
def test_flat_position_uses_entry_threshold(direction: float, expected: Action) -> None:
    assert resolve_action(scores(direction), portfolio(), CFG) is expected
