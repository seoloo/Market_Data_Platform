"""WA 공통 decide/위험/사이징 경로. 실데이터 손익은 사용하지 않는다."""

from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.features.test_wa import falling_window

from auto_trading.config.loader import load_config
from auto_trading.config.schema import RootConfig, WAConfig
from auto_trading.core.enums import (
    Action,
    Mode,
    OrderPurpose,
    PositionSide,
    PositionState,
    SystemState,
    Timeframe,
    VetoReason,
)
from auto_trading.core.types import (
    DataQuality,
    MarketSnapshot,
    PortfolioState,
    PositionSnapshot,
    TimeframeSeries,
)
from auto_trading.features.wa import WAState, evaluate_wa
from auto_trading.strategy.decision import DecisionContext, decide

D = Decimal


def config(direction: str = "long") -> RootConfig:
    data = load_config("backtest", apply_env=False).config.model_dump()
    data.update(strategy_id="wa_v1", wa={"direction": direction})
    data["risk"].update(tight_stop_action="REDUCE_POSITION")
    data["risk"]["take_profit"]["enabled"] = False
    # 합성 경로에서만 G5 장기 창을 생략한다. 실행 설정은 변경하지 않는다.
    data["risk"]["volatility_gate"]["enabled"] = False
    return RootConfig.model_validate(data)


def snapshot() -> MarketSnapshot:
    bars = falling_window()
    minutes = tuple(
        replace(
            bars[-1],
            open_ms=bars[-1].close_ms + 1 - (61 - i) * Timeframe.M1.ms,
            close_ms=bars[-1].close_ms - (60 - i) * Timeframe.M1.ms,
        )
        for i in range(61)
    )
    return MarketSnapshot(
        symbol="BTCUSDT",
        as_of_ms=bars[-1].close_ms,
        mode=Mode.BACKTEST,
        series=(TimeframeSeries(Timeframe.M1, minutes), TimeframeSeries(Timeframe.H1, bars)),
        mark_price=bars[-1].close,
        funding_rate=D(0),
        next_funding_ms=bars[-1].close_ms + Timeframe.H1.ms,
        spread_bps=D(1),
    )


def portfolio() -> PortfolioState:
    return PortfolioState(
        equity=D(10000),
        wallet_balance=D(10000),
        available_balance=D(10000),
        equity_high_water=D(10000),
    )


def context() -> DecisionContext:
    return DecisionContext(
        wa_state=WAState(),
        qty_step=D("0.001"),
        price_tick=D("0.01"),
        min_qty=D("0.001"),
        min_notional=D(5),
        maintenance_margin_rate=D("0.004"),
        event_calendar_available=True,
        research_unvalidated=True,
    )


@pytest.mark.parametrize(
    "direction, action", [("long", Action.OPEN_LONG), ("short", Action.OPEN_SHORT)]
)
def test_direction_stop_and_no_tp(direction: str, action: Action) -> None:
    cfg, snap = config(direction), snapshot()
    result = decide(snap, portfolio(), cfg, context())
    assert result.action is action
    assert not result.veto_reasons
    assert result.sizing is not None
    assert {x.purpose for x in result.intents} == {OrderPurpose.ENTRY, OrderPurpose.STOP}
    if direction == "long":
        assert result.sizing.stop_price < snap.mark_price
    else:
        assert result.sizing.stop_price > snap.mark_price
    assert result.sizing.stop_source == "wa_2sigma_6h"


@pytest.mark.parametrize(
    "offset, action", [(-1, Action.NONE), (0, Action.CLOSE), (1, Action.CLOSE)]
)
@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_exit_at_six_hours_despite_bad_data_and_halt(
    offset: int, action: Action, side: PositionSide
) -> None:
    snap = replace(snapshot(), data_quality=DataQuality(ok=False))
    pos = PositionSnapshot(
        symbol="BTCUSDT",
        side=side,
        state=PositionState.OPEN,
        qty=D(1),
        opened_ms=snap.as_of_ms - 360 * Timeframe.M1.ms - offset,
        entry_price=D(100),
    )
    result = decide(
        snap,
        replace(portfolio(), position=pos),
        config(),
        DecisionContext(system_state=SystemState.HALTED, killswitch_active=True),
    )
    assert result.action is action
    assert not result.veto_reasons
    if action is Action.CLOSE:
        assert result.intents[0].reduce_only
        assert result.intents[0].side is side.closing_side


def test_entry_keeps_calendar_and_data_gates() -> None:
    cfg, snap = config(), snapshot()
    result = decide(snap, portfolio(), cfg, replace(context(), event_calendar_available=False))
    assert VetoReason.EVENT_BLACKOUT in result.veto_reasons
    assert not result.intents
    assert (
        VetoReason.DATA_QUALITY
        in decide(replace(snap, funding_rate=None), portfolio(), cfg, context()).veto_reasons
    )
    assert not decide(snap, portfolio(), cfg, replace(context(), wa_state=None)).intents


def test_vetoed_trigger_still_consumes_lock_when_caller_saves_state() -> None:
    snap, cfg = snapshot(), config()
    assert cfg.wa is not None
    blocked = decide(snap, portfolio(), cfg, replace(context(), killswitch_active=True))
    assert blocked.veto_reasons and not blocked.intents
    next_state = evaluate_wa(
        falling_window(), as_of_ms=snap.as_of_ms, cfg=cfg.wa, state=WAState()
    ).state
    replay = decide(snap, portfolio(), cfg, replace(context(), wa_state=next_state))
    assert replay.action is Action.NONE and not replay.intents


def test_thousand_identical_inputs_produce_identical_decisions() -> None:
    snap, cfg, port, runtime = snapshot(), config(), portfolio(), context()
    expected = decide(snap, port, cfg, runtime).digest()
    assert {decide(snap, port, cfg, runtime).digest() for _ in range(1000)} == {expected}


@pytest.mark.parametrize("mode", [Mode.PAPER, Mode.TESTNET, Mode.LIVE])
def test_unvalidated_wa_cannot_run_outside_backtest(mode: Mode) -> None:
    data = config().model_dump()
    data["mode"] = mode
    with pytest.raises(ValidationError, match="backtest"):
        RootConfig.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("return_hours", 12),
        ("range_position_pct", "0.21"),
        ("hold_minutes", 120),
        ("trigger_lock_minutes", 60),
        ("stop_sigma_multiple", "1.0"),
    ],
)
def test_frozen_values_reject_tuning(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        WAConfig.model_validate({"direction": "long", field: value})


def test_direction_has_no_implicit_default() -> None:
    with pytest.raises(ValidationError):
        WAConfig.model_validate({})
