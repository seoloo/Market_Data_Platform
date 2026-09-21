"""도메인 열거형.

문자열 값은 DB·로그·설정에 그대로 저장되므로 **변경하면 과거 기록과 호환이 깨진다.**
값을 바꿔야 하면 마이그레이션을 함께 작성한다.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "Action",
    "CloseReason",
    "Mode",
    "OrderPurpose",
    "OrderState",
    "OrderType",
    "PositionSide",
    "PositionState",
    "Side",
    "SystemState",
    "TimeInForce",
    "Timeframe",
    "VetoReason",
]

_MINUTE_MS = 60_000


class Mode(StrEnum):
    """실행 모드. 같은 코드가 네 모드에서 동일하게 동작해야 한다."""

    BACKTEST = "backtest"
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"

    @property
    def is_real_money(self) -> bool:
        return self is Mode.LIVE


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def ms(self) -> int:
        """봉 하나의 길이 (밀리초)."""
        return _TIMEFRAME_MS[self]

    @property
    def minutes(self) -> int:
        return self.ms // _MINUTE_MS

    def open_ms_of(self, ts_ms: int) -> int:
        """``ts_ms`` 가 속한 봉의 시작 시각.

        UTC epoch 기준 정렬이며, 1d 를 포함해 모든 TF 가 epoch 로 나누어떨어진다.
        """
        return ts_ms - (ts_ms % self.ms)

    def close_ms_of(self, ts_ms: int) -> int:
        """``ts_ms`` 가 속한 봉의 마감 시각 (봉의 마지막 밀리초)."""
        return self.open_ms_of(ts_ms) + self.ms - 1

    def is_closed_at(self, open_ms: int, as_of_ms: int) -> bool:
        """``open_ms`` 에 시작한 봉이 ``as_of_ms`` 시점에 마감되었는가.

        형성 중인 봉을 전략 판단에 쓰지 않기 위한 기준 (AGENTS.md §3.2).
        """
        return open_ms + self.ms - 1 <= as_of_ms


_TIMEFRAME_MS: dict[Timeframe, int] = {
    Timeframe.M1: 1 * _MINUTE_MS,
    Timeframe.M5: 5 * _MINUTE_MS,
    Timeframe.M15: 15 * _MINUTE_MS,
    Timeframe.H1: 60 * _MINUTE_MS,
    Timeframe.H4: 240 * _MINUTE_MS,
    Timeframe.D1: 1440 * _MINUTE_MS,
}


class Side(StrEnum):
    """주문 방향."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class PositionSide(StrEnum):
    """포지션 방향. One-way 모드를 전제로 한다."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def sign(self) -> int:
        if self is PositionSide.LONG:
            return 1
        if self is PositionSide.SHORT:
            return -1
        return 0

    @property
    def closing_side(self) -> Side:
        """이 포지션을 줄이는 주문 방향."""
        if self is PositionSide.LONG:
            return Side.SELL
        if self is PositionSide.SHORT:
            return Side.BUY
        raise ValueError("FLAT 포지션에는 청산 방향이 없다")


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"

    @property
    def is_taker(self) -> bool:
        """수수료 계산용. LIMIT 만 maker 가 될 수 있다."""
        return self is not OrderType.LIMIT


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"  # post-only


class OrderPurpose(StrEnum):
    """주문의 목적. ``clientOrderId`` 에 인코딩된다 (04-state-machine.md §1)."""

    ENTRY = "E"
    EXIT = "X"
    STOP = "S"
    TAKE_PROFIT = "T"
    FLATTEN = "F"

    @property
    def is_protective(self) -> bool:
        return self in (OrderPurpose.STOP, OrderPurpose.TAKE_PROFIT)


class OrderState(StrEnum):
    """주문 상태머신 (04-state-machine.md §2)."""

    PENDING_NEW = "PENDING_NEW"
    SUBMITTING = "SUBMITTING"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    """응답 유실. 해소될 때까지 신규 진입 금지."""

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_ORDER_STATES

    @property
    def is_active(self) -> bool:
        """거래소에 살아 있어 아직 체결될 수 있는 상태."""
        return self in (OrderState.NEW, OrderState.PARTIALLY_FILLED)

    @property
    def blocks_new_entry(self) -> bool:
        return self is OrderState.UNKNOWN


_TERMINAL_ORDER_STATES = frozenset(
    {
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    }
)


class PositionState(StrEnum):
    """포지션 상태머신 (04-state-machine.md §3)."""

    FLAT = "FLAT"
    OPENING = "OPENING"
    UNPROTECTED = "UNPROTECTED"
    """체결됐으나 보호주문 미등록. 최대 위험 구간."""
    OPEN = "OPEN"
    REDUCING = "REDUCING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    RECONCILING = "RECONCILING"

    @property
    def has_exposure(self) -> bool:
        """실제 시장 노출이 있는 상태."""
        return self in _EXPOSED_POSITION_STATES

    @property
    def requires_protective_order(self) -> bool:
        """불변식 INV-1: 이 상태에서는 거래소에 reduce-only 스톱이 있어야 한다."""
        return self in (PositionState.OPEN, PositionState.REDUCING)


_EXPOSED_POSITION_STATES = frozenset(
    {
        PositionState.OPENING,
        PositionState.UNPROTECTED,
        PositionState.OPEN,
        PositionState.REDUCING,
        PositionState.CLOSING,
    }
)


class SystemState(StrEnum):
    """시스템 상태 (04-state-machine.md §5).

    어떤 상태에서도 안전 방향 동작(청산·스톱 등록)은 막히지 않는다.
    """

    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"

    @property
    def allows_new_entry(self) -> bool:
        return self is SystemState.RUNNING


class Action(StrEnum):
    """전략이 내린 결론."""

    NONE = "none"
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE = "close"
    REDUCE = "reduce"
    ADJUST_STOP = "adjust_stop"

    @property
    def is_entry(self) -> bool:
        return self in (Action.OPEN_LONG, Action.OPEN_SHORT)

    @property
    def is_safe_direction(self) -> bool:
        """리스크 게이트를 통과하지 않아도 되는 동작 (AGENTS.md §3.6)."""
        return self in (Action.CLOSE, Action.REDUCE, Action.ADJUST_STOP)


class CloseReason(StrEnum):
    SIGNAL = "signal"
    STOP = "stop"
    TAKE_PROFIT = "take_profit"
    RISK_HALT = "risk_halt"
    MANUAL = "manual"
    LIQUIDATION = "liquidation"
    UNKNOWN = "unknown"


class VetoReason(StrEnum):
    """Risk Engine 거부 사유 (05-risk-engine.md §2).

    거부 사유의 분포는 전략 진단의 1차 자료이므로 모두 기록한다.
    """

    # G1 시스템
    SYSTEM_NOT_RUNNING = "SYSTEM_NOT_RUNNING"
    KILLSWITCH = "KILLSWITCH"
    UNRESOLVED_ORDER = "UNRESOLVED_ORDER"
    DIRTY_CODE = "DIRTY_CODE"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    RECONCILE_FAILED = "RECONCILE_FAILED"

    # G2 데이터 품질
    STALE_DATA = "STALE_DATA"
    WARMUP = "WARMUP"
    CANDLE_GAP = "CANDLE_GAP"
    BAD_PRICE = "BAD_PRICE"
    WIDE_SPREAD = "WIDE_SPREAD"
    PRICE_DIVERGENCE = "PRICE_DIVERGENCE"
    DATA_QUALITY = "DATA_QUALITY"

    # G3 계좌 / 손실
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    COOLDOWN = "COOLDOWN"
    DAILY_TRADE_LIMIT = "DAILY_TRADE_LIMIT"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"

    # G4 포지션 / 노출
    POSITION_EXISTS = "POSITION_EXISTS"
    MAX_NOTIONAL = "MAX_NOTIONAL"
    MAX_LEVERAGE = "MAX_LEVERAGE"
    BELOW_MIN_SIZE = "BELOW_MIN_SIZE"
    MIN_HOLD_TIME = "MIN_HOLD_TIME"
    REENTRY_COOLDOWN = "REENTRY_COOLDOWN"
    SAME_DIRECTION_LIMIT = "SAME_DIRECTION_LIMIT"
    LIQUIDATION_TOO_CLOSE = "LIQUIDATION_TOO_CLOSE"
    SIZING_RESOLUTION = "SIZING_RESOLUTION"
    INVALID_STOP = "INVALID_STOP"
    STOP_TOO_TIGHT = "STOP_TOO_TIGHT"

    # G5 시장 상태
    VOL_TOO_HIGH = "VOL_TOO_HIGH"
    VOL_TOO_LOW = "VOL_TOO_LOW"
    RECENT_SPIKE = "RECENT_SPIKE"
    EXTREME_FUNDING = "EXTREME_FUNDING"
    FUNDING_IMMINENT = "FUNDING_IMMINENT"

    # G6 이벤트 블랙아웃
    EVENT_BLACKOUT = "EVENT_BLACKOUT"

    # G7 비용
    INSUFFICIENT_EDGE = "INSUFFICIENT_EDGE"

    # 전략 자체 판단
    BIAS_CONFLICT = "BIAS_CONFLICT"
    BELOW_ENTRY_THRESHOLD = "BELOW_ENTRY_THRESHOLD"
