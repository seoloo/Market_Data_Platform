"""G1~G7 리스크 게이트의 통과·경계·거부 테스트."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from auto_trading.config.schema import RiskConfig
from auto_trading.core.enums import (
    Action,
    Mode,
    OrderPurpose,
    OrderState,
    OrderType,
    PositionSide,
    PositionState,
    Side,
    SystemState,
    Timeframe,
    VetoReason,
)
from auto_trading.core.types import (
    DataQuality,
    MarketSnapshot,
    OrderSnapshot,
    PortfolioState,
    PositionSnapshot,
    SizingPlan,
)
from auto_trading.risk.gates import GateContext, RiskGateEngine, RiskState
from auto_trading.risk.sizing import PositionSizingEngine, SizingRequest

D = Decimal
AS_OF_MS = 10_000_000


def snapshot(**overrides: object) -> MarketSnapshot:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "as_of_ms": AS_OF_MS,
        "mode": Mode.BACKTEST,
        "series": (),
        "mark_price": D("100000"),
        "last_price": D("100000"),
        "funding_rate": D("0.0001"),
        "next_funding_ms": AS_OF_MS + 8 * 60 * 60 * 1000,
        "spread_bps": D("1"),
        "data_quality": DataQuality(ok=True),
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def portfolio(**overrides: object) -> PortfolioState:
    values: dict[str, object] = {
        "equity": D("1000"),
        "wallet_balance": D("1000"),
        "available_balance": D("1000"),
        "equity_high_water": D("1000"),
    }
    values.update(overrides)
    return PortfolioState(**values)  # type: ignore[arg-type]


def sizing_plan() -> SizingPlan:
    result = PositionSizingEngine(RiskConfig()).calculate(
        SizingRequest(
            side=PositionSide.LONG,
            equity=D("1000"),
            entry_price=D("100000"),
            stop_price=D("98000"),
            stop_source="swing_low",
            qty_step=D("0.00001"),
            price_tick=D("0.1"),
            min_qty=D("0.00001"),
            min_notional=D("5"),
            maintenance_margin_rate=D("0.004"),
        )
    )
    assert result.plan is not None
    return result.plan


def context(**overrides: object) -> GateContext:
    values: dict[str, object] = {
        "action": Action.OPEN_LONG,
        "system_state": SystemState.RUNNING,
        "portfolio": portfolio(),
        "snapshot": snapshot(),
        "risk_state": RiskState(day_start_equity=D("1000")),
        "sizing": sizing_plan(),
        "volatility_percentile": 0.5,
        "recent_price_change_pct": D("0.01"),
        "event_calendar_available": True,
        "event_blackout_active": False,
        "expected_edge_bps": D("10"),
        "required_edge_bps": D("5"),
    }
    values.update(overrides)
    return GateContext(**values)  # type: ignore[arg-type]


def reasons(ctx: GateContext, cfg: RiskConfig | None = None) -> tuple[VetoReason, ...]:
    return RiskGateEngine(cfg or RiskConfig()).evaluate(ctx).reasons


class TestHappyPathAndSafetyDirection:
    def test_complete_entry_context_passes(self):
        assert RiskGateEngine(RiskConfig()).evaluate(context()).approved

    @pytest.mark.parametrize("action", [Action.CLOSE, Action.REDUCE, Action.ADJUST_STOP])
    def test_safe_direction_bypasses_all_gates(self, action: Action):
        ctx = context(
            action=action,
            system_state=SystemState.HALTED,
            sizing=None,
            expected_edge_bps=None,
        )
        assert RiskGateEngine(RiskConfig()).evaluate(ctx).approved

    def test_none_action_does_not_create_a_trade(self):
        assert RiskGateEngine(RiskConfig()).evaluate(context(action=Action.NONE)).approved

    def test_unvalidated_research_bypasses_edge_only_in_backtest(self):
        research = context(
            research_unvalidated=True, expected_edge_bps=None, required_edge_bps=D("5")
        )
        assert VetoReason.INSUFFICIENT_EDGE not in reasons(research)
        live = context(
            research_unvalidated=True,
            expected_edge_bps=None,
            required_edge_bps=D("5"),
            snapshot=snapshot(mode=Mode.LIVE),
        )
        assert VetoReason.INSUFFICIENT_EDGE in reasons(live)


class TestSystemGates:
    @pytest.mark.parametrize(
        ("changes", "expected"),
        [
            ({"system_state": SystemState.DEGRADED}, VetoReason.SYSTEM_NOT_RUNNING),
            ({"killswitch_active": True}, VetoReason.KILLSWITCH),
            ({"code_dirty": True}, VetoReason.DIRTY_CODE),
            ({"clock_drift_ms": 1001}, VetoReason.CLOCK_DRIFT),
            ({"reconcile_ok": False}, VetoReason.RECONCILE_FAILED),
        ],
    )
    def test_system_vetoes(self, changes: dict[str, object], expected: VetoReason):
        if expected is VetoReason.DIRTY_CODE:
            changes["snapshot"] = snapshot(mode=Mode.LIVE)
        assert expected in reasons(context(**changes))

    def test_dirty_code_only_blocks_live(self):
        assert VetoReason.DIRTY_CODE not in reasons(context(code_dirty=True))

    def test_clock_drift_boundary_passes(self):
        assert VetoReason.CLOCK_DRIFT not in reasons(context(clock_drift_ms=1000))

    def test_unknown_order_blocks_entry(self):
        order = OrderSnapshot(
            client_order_id="x",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            purpose=OrderPurpose.ENTRY,
            qty=D("1"),
            state=OrderState.UNKNOWN,
        )
        assert VetoReason.UNRESOLVED_ORDER in reasons(
            context(portfolio=portfolio(open_orders=(order,)))
        )


class TestDataGates:
    @pytest.mark.parametrize(
        ("quality", "expected"),
        [
            (DataQuality(ok=False, stale_timeframes=(Timeframe.H1,)), VetoReason.STALE_DATA),
            (DataQuality(ok=False, warmup_ok=False), VetoReason.WARMUP),
            (DataQuality(ok=False, gapped_timeframes=(Timeframe.M5,)), VetoReason.CANDLE_GAP),
            (DataQuality(ok=False, notes=("unknown",)), VetoReason.DATA_QUALITY),
        ],
    )
    def test_quality_vetoes(self, quality: DataQuality, expected: VetoReason):
        assert expected in reasons(context(snapshot=snapshot(data_quality=quality)))

    def test_bad_price_flag(self):
        assert VetoReason.BAD_PRICE in reasons(context(bad_price=True))

    def test_spread_boundary_and_reject(self):
        assert VetoReason.WIDE_SPREAD not in reasons(context(snapshot=snapshot(spread_bps=D("5"))))
        assert VetoReason.WIDE_SPREAD in reasons(context(snapshot=snapshot(spread_bps=D("5.01"))))

    def test_mark_last_divergence(self):
        assert VetoReason.PRICE_DIVERGENCE in reasons(
            context(snapshot=snapshot(last_price=D("99000")))
        )

    def test_live_missing_microstructure_fails_closed(self):
        result = reasons(
            context(snapshot=snapshot(mode=Mode.LIVE, spread_bps=None, last_price=None))
        )
        assert VetoReason.DATA_QUALITY in result


class TestAccountGates:
    def test_daily_loss_boundary_vetoes(self):
        state = RiskState(day_start_equity=D("1000"), day_realized_pnl=D("-20"))
        assert VetoReason.DAILY_LOSS_LIMIT in reasons(context(risk_state=state))

    def test_drawdown_boundary_vetoes(self):
        pf = portfolio(equity=D("900"), equity_high_water=D("1000"))
        assert VetoReason.MAX_DRAWDOWN in reasons(context(portfolio=pf))

    def test_consecutive_loss_boundary_vetoes(self):
        state = RiskState(day_start_equity=D("1000"), consecutive_losses=4)
        assert VetoReason.CONSECUTIVE_LOSSES in reasons(context(risk_state=state))

    def test_cooldown_boundary_expires(self):
        active = RiskState(day_start_equity=D("1000"), cooldown_until_ms=AS_OF_MS + 1)
        expired = replace(active, cooldown_until_ms=AS_OF_MS)
        assert VetoReason.COOLDOWN in reasons(context(risk_state=active))
        assert VetoReason.COOLDOWN not in reasons(context(risk_state=expired))

    def test_daily_trade_limit_boundary_vetoes(self):
        state = RiskState(day_start_equity=D("1000"), day_trade_count=2)
        assert VetoReason.DAILY_TRADE_LIMIT in reasons(context(risk_state=state))

    def test_insufficient_margin_includes_buffer(self):
        assert VetoReason.INSUFFICIENT_MARGIN in reasons(
            context(portfolio=portfolio(available_balance=D("100")))
        )


class TestExposureGates:
    def test_existing_position_and_min_hold_both_reported(self):
        position = PositionSnapshot(
            symbol="BTCUSDT",
            side=PositionSide.SHORT,
            state=PositionState.OPEN,
            qty=D("0.01"),
            entry_price=D("100000"),
            opened_ms=AS_OF_MS - 60_000,
        )
        result = reasons(context(portfolio=portfolio(position=position)))
        assert VetoReason.POSITION_EXISTS in result
        assert VetoReason.MIN_HOLD_TIME in result

    def test_reentry_cooldown_boundary(self):
        one_minute = 60_000
        active = RiskState(day_start_equity=D("1000"), last_exit_ms=AS_OF_MS - 59 * one_minute)
        expired = replace(active, last_exit_ms=AS_OF_MS - 60 * one_minute)
        assert VetoReason.REENTRY_COOLDOWN in reasons(context(risk_state=active))
        assert VetoReason.REENTRY_COOLDOWN not in reasons(context(risk_state=expired))

    def test_same_direction_limit(self):
        state = RiskState(
            day_start_equity=D("1000"),
            last_entry_side=PositionSide.LONG,
            same_direction_entries=2,
        )
        assert VetoReason.SAME_DIRECTION_LIMIT in reasons(context(risk_state=state))

    @pytest.mark.parametrize(
        "sizing_reason",
        [
            VetoReason.BELOW_MIN_SIZE,
            VetoReason.SIZING_RESOLUTION,
            VetoReason.INVALID_STOP,
            VetoReason.STOP_TOO_TIGHT,
            VetoReason.MAX_LEVERAGE,
            VetoReason.LIQUIDATION_TOO_CLOSE,
        ],
    )
    def test_sizing_veto_is_propagated(self, sizing_reason: VetoReason):
        result = reasons(context(sizing=None, sizing_veto_reasons=(sizing_reason,)))
        assert sizing_reason in result


class TestMarketEventAndCostGates:
    def test_volatility_bounds_are_strict(self):
        assert VetoReason.VOL_TOO_LOW not in reasons(context(volatility_percentile=0.05))
        assert VetoReason.VOL_TOO_HIGH not in reasons(context(volatility_percentile=0.98))
        assert VetoReason.VOL_TOO_LOW in reasons(context(volatility_percentile=0.049))
        assert VetoReason.VOL_TOO_HIGH in reasons(context(volatility_percentile=0.981))

    def test_missing_volatility_fails_closed(self):
        assert VetoReason.DATA_QUALITY in reasons(context(volatility_percentile=None))

    def test_recent_spike_boundary_vetoes(self):
        assert VetoReason.RECENT_SPIKE in reasons(context(recent_price_change_pct=D("-0.03")))

    def test_extreme_funding(self):
        assert VetoReason.EXTREME_FUNDING in reasons(
            context(snapshot=snapshot(funding_rate=D("0.0011")))
        )

    def test_imminent_adverse_funding_long_and_short(self):
        imminent = AS_OF_MS + 5 * 60 * 1000
        long_reasons = reasons(
            context(snapshot=snapshot(funding_rate=D("0.0001"), next_funding_ms=imminent))
        )
        short_reasons = reasons(
            context(
                action=Action.OPEN_SHORT,
                snapshot=snapshot(funding_rate=D("-0.0001"), next_funding_ms=imminent),
            )
        )
        assert VetoReason.FUNDING_IMMINENT in long_reasons
        assert VetoReason.FUNDING_IMMINENT in short_reasons

    def test_missing_funding_fails_closed(self):
        assert VetoReason.DATA_QUALITY in reasons(context(snapshot=snapshot(funding_rate=None)))

    @pytest.mark.parametrize(
        "changes",
        [
            {"event_blackout_active": True},
            {"event_calendar_available": False},
        ],
    )
    def test_event_blackout_fail_closed(self, changes: dict[str, bool]):
        assert VetoReason.EVENT_BLACKOUT in reasons(context(**changes))

    def test_edge_must_strictly_exceed_requirement(self):
        assert VetoReason.INSUFFICIENT_EDGE in reasons(
            context(expected_edge_bps=D("5"), required_edge_bps=D("5"))
        )
        assert VetoReason.INSUFFICIENT_EDGE not in reasons(
            context(expected_edge_bps=D("5.01"), required_edge_bps=D("5"))
        )

    def test_all_veto_reasons_are_collected_without_duplicates(self):
        result = reasons(
            context(
                system_state=SystemState.HALTED,
                killswitch_active=True,
                volatility_percentile=None,
                recent_price_change_pct=None,
                event_blackout_active=True,
                expected_edge_bps=None,
                sizing=None,
            )
        )
        assert VetoReason.SYSTEM_NOT_RUNNING in result
        assert VetoReason.KILLSWITCH in result
        assert VetoReason.DATA_QUALITY in result
        assert VetoReason.EVENT_BLACKOUT in result
        assert VetoReason.INSUFFICIENT_EDGE in result
        assert len(result) == len(set(result))
