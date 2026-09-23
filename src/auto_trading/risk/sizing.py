"""D9 포지션 사이징 — 손절을 먼저 정하고 명목가와 레버리지를 역산한다.

이 모듈은 순수하다. 거래소 필터와 유지증거금률은 호출자가 스냅샷으로 넘기며,
네트워크·DB·시스템 시각에 접근하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from auto_trading.config.schema import (
    LeverageLimitAction,
    RiskConfig,
    StopMode,
    StopPick,
    TightStopAction,
)
from auto_trading.core.enums import PositionSide, VetoReason
from auto_trading.core.money import ONE, ZERO, floor_to_step, round_to_tick, safe_div
from auto_trading.core.types import RiskVerdict, SizingPlan

__all__ = [
    "PositionSizingEngine",
    "SizingRequest",
    "SizingResult",
    "StopSelection",
]


@dataclass(frozen=True, slots=True)
class StopSelection:
    """스윙/ATR 규칙으로 선택한 손절가와 근거."""

    price: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class SizingRequest:
    """한 번의 사이징에 필요한 외부 입력.

    ``maintenance_margin_rate`` 는 Binance leverage bracket에서, 수량·가격 필터는
    exchangeInfo에서 얻되 이 객체에는 이미 정규화된 ``Decimal`` 로 전달한다.
    """

    side: PositionSide
    equity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    stop_source: str
    qty_step: Decimal
    price_tick: Decimal
    min_qty: Decimal
    min_notional: Decimal
    maintenance_margin_rate: Decimal
    expected_funding_rate: Decimal | None = None
    risk_scale: Decimal = ONE
    """국면별 리스크 축소 계수 (D14). 거래당 허용 손실에만 곱한다, (0,1].

    고변동 국면에서 "작게 걸기" 를 표현하는 유일한 지점이다. 점수 쪽에 곱하면
    고정 진입 임계값과 만나 거래 자체가 불가능해진다 (strategy/bias.py 참조).
    """

    def __post_init__(self) -> None:
        if not ZERO < self.risk_scale <= ONE:
            raise ValueError(f"risk_scale 은 (0,1] 이어야 한다: {self.risk_scale}")


@dataclass(frozen=True, slots=True)
class SizingResult:
    """사이징 성공 계획 또는 fail-closed veto."""

    verdict: RiskVerdict
    plan: SizingPlan | None = None

    def __post_init__(self) -> None:
        if self.verdict.approved != (self.plan is not None):
            raise ValueError("승인 여부와 SizingPlan 존재 여부가 일치하지 않는다")

    @classmethod
    def approve(cls, plan: SizingPlan) -> SizingResult:
        return cls(verdict=RiskVerdict.approve(), plan=plan)

    @classmethod
    def veto(cls, *reasons: VetoReason) -> SizingResult:
        return cls(verdict=RiskVerdict.veto(*reasons))


class PositionSizingEngine:
    """D9 닫힌 해와 거래소 제약을 적용하는 결정론적 계산기."""

    __slots__ = ("_cfg",)

    def __init__(self, cfg: RiskConfig) -> None:
        self._cfg = cfg

    def select_stop(
        self,
        *,
        side: PositionSide,
        entry_price: Decimal,
        atr: Decimal,
        price_tick: Decimal,
        confirmed_swing_price: Decimal | None = None,
    ) -> StopSelection:
        """ATR 손절과 확정 스윙 손절 중 설정된 규칙으로 하나를 고른다.

        구조 가격은 반드시 ``confirmed_at_ms <= as_of_ms`` 검증을 마친 값이어야 한다.
        이 함수는 시각을 직접 읽지 않으므로 repainting 가능성이 있는 스윙을 만들 수 없다.
        """
        self._validate_side_and_prices(side, entry_price, price_tick)
        if atr <= ZERO:
            raise ValueError(f"atr 는 0보다 커야 한다: {atr}")

        if side is PositionSide.LONG:
            atr_price = entry_price - atr * self._cfg.stop.atr_multiple
            structure_price = (
                confirmed_swing_price - atr * self._cfg.stop.swing_buffer_atr
                if confirmed_swing_price is not None
                else None
            )
            rounding = ROUND_FLOOR
        else:
            atr_price = entry_price + atr * self._cfg.stop.atr_multiple
            structure_price = (
                confirmed_swing_price + atr * self._cfg.stop.swing_buffer_atr
                if confirmed_swing_price is not None
                else None
            )
            rounding = ROUND_CEILING

        candidates = [(atr_price, "atr")]
        if self._cfg.stop.mode is StopMode.STRUCTURE_OR_ATR and structure_price is not None:
            swing_name = "swing_low" if side is PositionSide.LONG else "swing_high"
            candidates.append((structure_price, swing_name))

        if self._cfg.stop.pick is StopPick.WIDER:
            selected = min(candidates) if side is PositionSide.LONG else max(candidates)
        else:
            selected = max(candidates) if side is PositionSide.LONG else min(candidates)

        price = round_to_tick(selected[0], price_tick, rounding=rounding)
        return StopSelection(price=price, source=selected[1])

    def calculate(self, request: SizingRequest) -> SizingResult:
        """손절가에서 최대손실을 역산하고 거래소 필터까지 적용한다."""
        self._validate_request(request)

        rounding = ROUND_FLOOR if request.side is PositionSide.LONG else ROUND_CEILING
        stop_price = round_to_tick(request.stop_price, request.price_tick, rounding=rounding)
        stop_distance = self._stop_distance(request.side, request.entry_price, stop_price)
        if stop_distance <= ZERO:
            return SizingResult.veto(VetoReason.INVALID_STOP)

        stop_widened = False
        if stop_distance < self._cfg.min_stop_distance:
            if self._cfg.tight_stop_action is TightStopAction.SKIP:
                return SizingResult.veto(VetoReason.STOP_TOO_TIGHT)
            if self._cfg.tight_stop_action is TightStopAction.WIDEN:
                stop_widened = True
                if request.side is PositionSide.LONG:
                    stop_price = request.entry_price * (ONE - self._cfg.min_stop_distance)
                else:
                    stop_price = request.entry_price * (ONE + self._cfg.min_stop_distance)
                stop_price = round_to_tick(stop_price, request.price_tick, rounding=rounding)
                stop_distance = self._stop_distance(request.side, request.entry_price, stop_price)

        effective_risk = self._cfg.risk_per_trade * request.risk_scale
        max_loss = request.equity * effective_risk
        cost_in = self._cfg.cost.cost_rate_in
        cost_out = self._cfg.cost.cost_rate_out
        funding = self._funding_rate(request.expected_funding_rate)
        actual_denominator = stop_distance * (ONE + cost_out) + cost_in + cost_out + funding
        sizing_distance = (
            max(stop_distance, self._cfg.min_stop_distance)
            if self._cfg.tight_stop_action is TightStopAction.REDUCE_POSITION
            else stop_distance
        )
        sizing_denominator = sizing_distance * (ONE + cost_out) + cost_in + cost_out + funding
        target_notional = safe_div(max_loss, sizing_denominator)
        margin_cap = request.equity * self._cfg.margin_allocation

        required_leverage = self._ceil_ratio(target_notional, margin_cap)
        leverage_capped = required_leverage > self._cfg.max_leverage
        sizing_notional = target_notional
        if leverage_capped:
            if self._cfg.leverage_limit_action is LeverageLimitAction.SKIP_TRADE:
                return SizingResult.veto(VetoReason.MAX_LEVERAGE)
            sizing_notional = margin_cap * self._cfg.max_leverage

        qty = floor_to_step(sizing_notional / request.entry_price, request.qty_step)
        actual_notional = qty * request.entry_price
        if qty < request.min_qty or actual_notional < request.min_notional:
            return SizingResult.veto(VetoReason.BELOW_MIN_SIZE)

        rounding_error = safe_div(sizing_notional - actual_notional, sizing_notional)
        if rounding_error > self._cfg.max_rounding_error:
            return SizingResult.veto(VetoReason.SIZING_RESOLUTION)

        exchange_leverage = self._ceil_ratio(actual_notional, margin_cap)
        if exchange_leverage > self._cfg.max_leverage:
            return SizingResult.veto(VetoReason.MAX_LEVERAGE)

        liq_distance = ONE / Decimal(exchange_leverage) - request.maintenance_margin_rate
        if liq_distance <= ZERO:
            return SizingResult.veto(VetoReason.LIQUIDATION_TOO_CLOSE)
        liq_buffer_ratio = safe_div(liq_distance, stop_distance)
        if liq_distance < self._cfg.liquidation_safety_buffer * stop_distance:
            return SizingResult.veto(VetoReason.LIQUIDATION_TOO_CLOSE)

        estimated_loss = actual_notional * actual_denominator
        margin_allocated = actual_notional / Decimal(exchange_leverage)
        effective_leverage = actual_notional / request.equity
        if request.side is PositionSide.LONG:
            liquidation_price = max(ZERO, request.entry_price * (ONE - liq_distance))
        else:
            liquidation_price = request.entry_price * (ONE + liq_distance)

        plan = SizingPlan(
            account_equity=request.equity,
            risk_per_trade=effective_risk,
            max_loss=max_loss,
            entry_price=request.entry_price,
            stop_price=stop_price,
            stop_distance_pct=stop_distance,
            stop_source=request.stop_source,
            stop_widened=stop_widened,
            cost_rate_in=cost_in,
            cost_rate_out=cost_out,
            expected_funding_rate=funding,
            target_notional=target_notional,
            actual_notional=actual_notional,
            qty=qty,
            margin_cap=margin_cap,
            margin_allocated=margin_allocated,
            exchange_leverage=exchange_leverage,
            effective_leverage=effective_leverage,
            estimated_loss_at_stop=estimated_loss,
            liquidation_price=liquidation_price,
            liq_distance_pct=liq_distance,
            liq_buffer_ratio=liq_buffer_ratio,
            leverage_capped=leverage_capped,
            rounding_error=rounding_error,
        )
        return SizingResult.approve(plan)

    def _funding_rate(self, override: Decimal | None) -> Decimal:
        if not self._cfg.cost.include_funding:
            return ZERO
        rate = self._cfg.cost.expected_funding_rate if override is None else override
        return max(ZERO, rate)

    @staticmethod
    def _ceil_ratio(numerator: Decimal, denominator: Decimal) -> int:
        return max(
            1,
            int((numerator / denominator).to_integral_value(rounding=ROUND_CEILING)),
        )

    @staticmethod
    def _stop_distance(side: PositionSide, entry_price: Decimal, stop_price: Decimal) -> Decimal:
        if side is PositionSide.LONG:
            return (entry_price - stop_price) / entry_price
        return (stop_price - entry_price) / entry_price

    @staticmethod
    def _validate_side_and_prices(
        side: PositionSide, entry_price: Decimal, price_tick: Decimal
    ) -> None:
        if side is PositionSide.FLAT:
            raise ValueError("FLAT 방향은 사이징할 수 없다")
        if entry_price <= ZERO:
            raise ValueError(f"entry_price 는 0보다 커야 한다: {entry_price}")
        if price_tick <= ZERO:
            raise ValueError(f"price_tick 은 0보다 커야 한다: {price_tick}")

    def _validate_request(self, request: SizingRequest) -> None:
        self._validate_side_and_prices(request.side, request.entry_price, request.price_tick)
        if request.equity <= ZERO:
            raise ValueError(f"equity 는 0보다 커야 한다: {request.equity}")
        if request.stop_price <= ZERO:
            raise ValueError(f"stop_price 는 0보다 커야 한다: {request.stop_price}")
        if request.qty_step <= ZERO:
            raise ValueError(f"qty_step 은 0보다 커야 한다: {request.qty_step}")
        if request.min_qty < ZERO or request.min_notional < ZERO:
            raise ValueError("거래소 최소 수량/명목가는 음수일 수 없다")
        if not ZERO <= request.maintenance_margin_rate < ONE:
            raise ValueError(
                f"maintenance_margin_rate 는 [0,1) 범위여야 한다: {request.maintenance_margin_rate}"
            )
