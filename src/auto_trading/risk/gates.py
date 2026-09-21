"""진입 전 하드 리스크 게이트.

각 그룹은 입력을 읽기만 하는 순수 함수다. 청산·축소·스톱 조정은 어떤 상태에서도
막지 않으며, 진입 평가에서는 첫 실패에서 중단하지 않고 모든 veto 사유를 수집한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from auto_trading.config.schema import RiskConfig
from auto_trading.core.enums import (
    Action,
    Mode,
    PositionSide,
    SystemState,
    VetoReason,
)
from auto_trading.core.money import ZERO
from auto_trading.core.types import MarketSnapshot, PortfolioState, RiskVerdict, SizingPlan

__all__ = ["GateContext", "RiskGateEngine", "RiskState"]

_BPS = Decimal("10000")
_MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class RiskState:
    """DB에서 복원해 게이트에 전달하는 누적 리스크 상태."""

    day_start_equity: Decimal = ZERO
    day_realized_pnl: Decimal = ZERO
    consecutive_losses: int = 0
    cooldown_until_ms: int | None = None
    day_trade_count: int = 0
    last_exit_ms: int | None = None
    last_entry_side: PositionSide | None = None
    same_direction_entries: int = 0

    def __post_init__(self) -> None:
        for name in ("consecutive_losses", "day_trade_count", "same_direction_entries"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 는 음수일 수 없다")


@dataclass(frozen=True, slots=True)
class GateContext:
    """한 번의 진입 게이트 평가에 필요한 완전한 입력."""

    action: Action
    system_state: SystemState
    portfolio: PortfolioState
    snapshot: MarketSnapshot
    risk_state: RiskState = field(default_factory=RiskState)
    sizing: SizingPlan | None = None
    sizing_veto_reasons: tuple[VetoReason, ...] = ()
    killswitch_active: bool = False
    code_dirty: bool = False
    clock_drift_ms: int = 0
    reconcile_ok: bool = True
    bad_price: bool = False
    volatility_percentile: float | None = None
    recent_price_change_pct: Decimal | None = None
    event_blackout_active: bool = False
    event_calendar_available: bool = True
    expected_edge_bps: Decimal | None = None
    required_edge_bps: Decimal | None = None


class RiskGateEngine:
    """G1~G7을 문서 순서대로 평가하는 순수 엔진."""

    __slots__ = ("_cfg", "_clock_drift_limit_ms")

    def __init__(self, cfg: RiskConfig, *, clock_drift_limit_ms: int = 1000) -> None:
        if clock_drift_limit_ms < 0:
            raise ValueError("clock_drift_limit_ms 는 음수일 수 없다")
        self._cfg = cfg
        self._clock_drift_limit_ms = clock_drift_limit_ms

    def evaluate(self, context: GateContext) -> RiskVerdict:
        """진입이면 모든 게이트를 평가하고, 안전 방향 동작이면 즉시 승인한다."""
        if context.action.is_safe_direction or not context.action.is_entry:
            return RiskVerdict.approve()

        reasons = [
            *self._system_reasons(context),
            *self._data_reasons(context),
            *self._account_reasons(context),
            *self._exposure_reasons(context),
            *self._market_reasons(context),
            *self._event_reasons(context),
            *self._cost_reasons(context),
        ]
        unique = tuple(dict.fromkeys(reasons))
        return RiskVerdict.veto(*unique) if unique else RiskVerdict.approve()

    def _system_reasons(self, context: GateContext) -> list[VetoReason]:
        reasons: list[VetoReason] = []
        if not context.system_state.allows_new_entry:
            reasons.append(VetoReason.SYSTEM_NOT_RUNNING)
        if context.killswitch_active:
            reasons.append(VetoReason.KILLSWITCH)
        if context.portfolio.has_unknown_orders:
            reasons.append(VetoReason.UNRESOLVED_ORDER)
        if context.code_dirty and context.snapshot.mode is Mode.LIVE:
            reasons.append(VetoReason.DIRTY_CODE)
        if abs(context.clock_drift_ms) > self._clock_drift_limit_ms:
            reasons.append(VetoReason.CLOCK_DRIFT)
        if not context.reconcile_ok:
            reasons.append(VetoReason.RECONCILE_FAILED)
        return reasons

    def _data_reasons(self, context: GateContext) -> list[VetoReason]:
        quality = context.snapshot.data_quality
        reasons: list[VetoReason] = []
        if quality.stale_timeframes:
            reasons.append(VetoReason.STALE_DATA)
        if not quality.warmup_ok:
            reasons.append(VetoReason.WARMUP)
        if quality.gapped_timeframes:
            reasons.append(VetoReason.CANDLE_GAP)
        if context.bad_price:
            reasons.append(VetoReason.BAD_PRICE)

        spread = context.snapshot.spread_bps
        last_price = context.snapshot.last_price
        live_fields_required = context.snapshot.mode in (Mode.PAPER, Mode.TESTNET, Mode.LIVE)
        if spread is None:
            if live_fields_required:
                reasons.append(VetoReason.DATA_QUALITY)
        elif spread > self._cfg.max_spread_bps:
            reasons.append(VetoReason.WIDE_SPREAD)

        if last_price is None:
            if live_fields_required:
                reasons.append(VetoReason.DATA_QUALITY)
        elif last_price <= ZERO:
            reasons.append(VetoReason.BAD_PRICE)
        else:
            divergence_bps = abs(context.snapshot.mark_price - last_price) / last_price * _BPS
            if divergence_bps > self._cfg.max_price_divergence_bps:
                reasons.append(VetoReason.PRICE_DIVERGENCE)

        has_specific_quality_reason = any(
            reason
            in (
                VetoReason.STALE_DATA,
                VetoReason.WARMUP,
                VetoReason.CANDLE_GAP,
                VetoReason.BAD_PRICE,
            )
            for reason in reasons
        )
        if not quality.ok and not has_specific_quality_reason:
            reasons.append(VetoReason.DATA_QUALITY)
        return reasons

    def _account_reasons(self, context: GateContext) -> list[VetoReason]:
        reasons: list[VetoReason] = []
        state = context.risk_state
        portfolio = context.portfolio
        day_base = state.day_start_equity if state.day_start_equity > ZERO else portfolio.equity
        day_pnl = state.day_realized_pnl + portfolio.unrealized_pnl
        if day_base > ZERO and day_pnl <= -(day_base * self._cfg.daily_loss_limit_pct):
            reasons.append(VetoReason.DAILY_LOSS_LIMIT)
        if portfolio.drawdown_pct >= self._cfg.max_drawdown_pct:
            reasons.append(VetoReason.MAX_DRAWDOWN)
        if state.consecutive_losses >= self._cfg.consecutive_loss_limit:
            reasons.append(VetoReason.CONSECUTIVE_LOSSES)
        if (
            state.cooldown_until_ms is not None
            and context.snapshot.as_of_ms < state.cooldown_until_ms
        ):
            reasons.append(VetoReason.COOLDOWN)
        if state.day_trade_count >= self._cfg.daily_trade_limit:
            reasons.append(VetoReason.DAILY_TRADE_LIMIT)
        if context.sizing is not None:
            required = context.sizing.margin_allocated * self._cfg.margin_availability_buffer
            if portfolio.available_balance < required:
                reasons.append(VetoReason.INSUFFICIENT_MARGIN)
        return reasons

    def _exposure_reasons(self, context: GateContext) -> list[VetoReason]:
        reasons: list[VetoReason] = []
        entry_side = self._entry_side(context.action)
        position = context.portfolio.position
        if not context.portfolio.is_flat:
            reasons.append(VetoReason.POSITION_EXISTS)

        plan = context.sizing
        if plan is None:
            if context.sizing_veto_reasons:
                reasons.extend(context.sizing_veto_reasons)
            else:
                reasons.append(VetoReason.DATA_QUALITY)
        else:
            max_notional = context.portfolio.equity * self._cfg.max_notional_multiple
            if plan.actual_notional > max_notional:
                reasons.append(VetoReason.MAX_NOTIONAL)
            if plan.exchange_leverage > self._cfg.max_leverage:
                reasons.append(VetoReason.MAX_LEVERAGE)
            if plan.liq_buffer_ratio < self._cfg.liquidation_safety_buffer:
                reasons.append(VetoReason.LIQUIDATION_TOO_CLOSE)
            if plan.rounding_error > self._cfg.max_rounding_error:
                reasons.append(VetoReason.SIZING_RESOLUTION)

        if position is not None and not position.is_flat and position.side is not entry_side:
            min_hold_ms = self._cfg.min_hold_minutes * _MINUTE_MS
            if position.holding_ms(context.snapshot.as_of_ms) < min_hold_ms:
                reasons.append(VetoReason.MIN_HOLD_TIME)

        state = context.risk_state
        if state.last_exit_ms is not None:
            cooldown_ms = self._cfg.reentry_cooldown_minutes * _MINUTE_MS
            if context.snapshot.as_of_ms - state.last_exit_ms < cooldown_ms:
                reasons.append(VetoReason.REENTRY_COOLDOWN)
        if (
            state.last_entry_side is entry_side
            and state.same_direction_entries >= self._cfg.same_direction_limit
        ):
            reasons.append(VetoReason.SAME_DIRECTION_LIMIT)
        return reasons

    def _market_reasons(self, context: GateContext) -> list[VetoReason]:
        reasons: list[VetoReason] = []
        gate = self._cfg.volatility_gate
        percentile = context.volatility_percentile
        if gate.enabled:
            if percentile is None:
                reasons.append(VetoReason.DATA_QUALITY)
            elif percentile > gate.atr_pct_upper:
                reasons.append(VetoReason.VOL_TOO_HIGH)
            elif percentile < gate.atr_pct_lower:
                reasons.append(VetoReason.VOL_TOO_LOW)

        price_change = context.recent_price_change_pct
        if price_change is None:
            reasons.append(VetoReason.DATA_QUALITY)
        elif abs(price_change) >= self._cfg.recent_spike_pct:
            reasons.append(VetoReason.RECENT_SPIKE)

        funding = context.snapshot.funding_rate
        next_funding_ms = context.snapshot.next_funding_ms
        if funding is None or next_funding_ms is None:
            reasons.append(VetoReason.DATA_QUALITY)
        else:
            if abs(funding) > self._cfg.extreme_funding_rate:
                reasons.append(VetoReason.EXTREME_FUNDING)
            time_to_funding_ms = next_funding_ms - context.snapshot.as_of_ms
            imminent_ms = self._cfg.funding_imminent_min * _MINUTE_MS
            entry_side = self._entry_side(context.action)
            adverse = (entry_side is PositionSide.LONG and funding > ZERO) or (
                entry_side is PositionSide.SHORT and funding < ZERO
            )
            if ZERO <= Decimal(time_to_funding_ms) <= Decimal(imminent_ms) and adverse:
                reasons.append(VetoReason.FUNDING_IMMINENT)
        return reasons

    @staticmethod
    def _event_reasons(context: GateContext) -> list[VetoReason]:
        if not context.event_calendar_available or context.event_blackout_active:
            return [VetoReason.EVENT_BLACKOUT]
        return []

    @staticmethod
    def _cost_reasons(context: GateContext) -> list[VetoReason]:
        expected = context.expected_edge_bps
        required = context.required_edge_bps
        if expected is None or required is None or expected <= required:
            return [VetoReason.INSUFFICIENT_EDGE]
        return []

    @staticmethod
    def _entry_side(action: Action) -> PositionSide:
        if action is Action.OPEN_LONG:
            return PositionSide.LONG
        if action is Action.OPEN_SHORT:
            return PositionSide.SHORT
        raise ValueError(f"진입 action 이 아니다: {action.value}")
