"""도메인 타입.

모두 ``frozen=True`` 불변 dataclass 다. Pure Core 를 통과하는 값이 도중에 바뀌면
재현성이 깨지므로 변경 불가능하게 만든다.

검증은 ``__post_init__`` 에서 수행한다. 잘못된 시장 데이터가 타입 경계를 넘어오지
못하게 하는 것이 목적이다 (09-failure-modes.md F9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from auto_trading.core.enums import (
    Action,
    Mode,
    OrderPurpose,
    OrderState,
    OrderType,
    PositionSide,
    PositionState,
    Side,
    Timeframe,
    VetoReason,
)
from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.core.hashing import sha256_hex
from auto_trading.core.money import ZERO, safe_div

__all__ = [
    "Candle",
    "Component",
    "DataQuality",
    "Decision",
    "Intent",
    "MarketSnapshot",
    "OrderSnapshot",
    "PortfolioState",
    "PositionSnapshot",
    "RiskVerdict",
    "ScoreSet",
    "TimeframeSeries",
]


# ---------------------------------------------------------------- 시장 데이터


@dataclass(frozen=True, slots=True)
class Candle:
    """마감된 봉 하나.

    ``close_ms`` 는 봉의 **마지막 밀리초**다 (open_ms + tf.ms - 1).
    """

    open_ms: int
    close_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal = ZERO
    trade_count: int = 0

    def __post_init__(self) -> None:
        if self.close_ms <= self.open_ms:
            raise DataIntegrityError(f"close_ms <= open_ms: {self.open_ms} -> {self.close_ms}")
        for name in ("open", "high", "low", "close"):
            price: Decimal = getattr(self, name)
            if price <= ZERO:
                raise DataIntegrityError(f"{name} 가 0 이하: {price} (open_ms={self.open_ms})")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise DataIntegrityError(
                f"OHLC 관계 위반 (open_ms={self.open_ms}): "
                f"O={self.open} H={self.high} L={self.low} C={self.close}"
            )
        if self.high < self.low:
            raise DataIntegrityError(f"high < low: {self.high} < {self.low}")
        if self.volume < ZERO or self.quote_volume < ZERO:
            raise DataIntegrityError(f"거래량이 음수 (open_ms={self.open_ms})")

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "o_ms": self.open_ms,
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
        }


@dataclass(frozen=True, slots=True)
class TimeframeSeries:
    """한 타임프레임의 마감봉 시계열. 시간 오름차순, 중복 없음."""

    timeframe: Timeframe
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        prev_open = -1
        for candle in self.candles:
            if candle.open_ms <= prev_open:
                raise DataIntegrityError(
                    f"{self.timeframe.value} 봉이 오름차순이 아니거나 중복: {candle.open_ms}"
                )
            if candle.open_ms % self.timeframe.ms != 0:
                raise DataIntegrityError(
                    f"{self.timeframe.value} 봉 시작 시각이 정렬되지 않음: {candle.open_ms}"
                )
            prev_open = candle.open_ms

    def __len__(self) -> int:
        return len(self.candles)

    @property
    def last(self) -> Candle | None:
        return self.candles[-1] if self.candles else None

    @property
    def closes(self) -> tuple[Decimal, ...]:
        return tuple(c.close for c in self.candles)

    @property
    def highs(self) -> tuple[Decimal, ...]:
        return tuple(c.high for c in self.candles)

    @property
    def lows(self) -> tuple[Decimal, ...]:
        return tuple(c.low for c in self.candles)

    @property
    def volumes(self) -> tuple[Decimal, ...]:
        return tuple(c.volume for c in self.candles)

    def has_gaps(self) -> bool:
        """연속된 봉 사이에 결측이 있는가."""
        step = self.timeframe.ms
        return any(
            self.candles[i].open_ms - self.candles[i - 1].open_ms != step
            for i in range(1, len(self.candles))
        )

    def to_digest_dict(self) -> dict[str, Any]:
        return {"tf": self.timeframe.value, "candles": list(self.candles)}


@dataclass(frozen=True, slots=True)
class DataQuality:
    """스냅샷의 데이터 품질. ``ok`` 가 False 면 진입을 거부한다 (fail-closed)."""

    ok: bool
    warmup_ok: bool = True
    stale_timeframes: tuple[Timeframe, ...] = ()
    missing_timeframes: tuple[Timeframe, ...] = ()
    gapped_timeframes: tuple[Timeframe, ...] = ()
    notes: tuple[str, ...] = ()

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "warmup": self.warmup_ok,
            "stale": list(self.stale_timeframes),
            "missing": list(self.missing_timeframes),
            "gapped": list(self.gapped_timeframes),
        }


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """``as_of_ms`` 시점에 **알 수 있었던** 시장 상태 전부.

    Pure Core 의 유일한 시장 데이터 입력이다. 여기 없는 데이터는 전략이 볼 수 없다.
    이 경계가 look-ahead 를 구조적으로 차단한다 (AGENTS.md §3.2).
    """

    symbol: str
    as_of_ms: int
    mode: Mode
    series: tuple[TimeframeSeries, ...]
    mark_price: Decimal
    last_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_ms: int | None = None
    spread_bps: Decimal | None = None
    data_quality: DataQuality = field(default_factory=lambda: DataQuality(ok=True))

    def __post_init__(self) -> None:
        if self.mark_price <= ZERO:
            raise DataIntegrityError(f"mark_price 가 0 이하: {self.mark_price}")
        seen: set[Timeframe] = set()
        for s in self.series:
            if s.timeframe in seen:
                raise DataIntegrityError(f"타임프레임 중복: {s.timeframe.value}")
            seen.add(s.timeframe)
        self.validate_causal()

    def validate_causal(self) -> None:
        """모든 봉이 ``as_of_ms`` 이전에 마감되었는지 검증한다.

        위반하면 미래 데이터를 보고 있다는 뜻이므로 즉시 실패시킨다.
        """
        for s in self.series:
            last = s.last
            if last is not None and last.close_ms > self.as_of_ms:
                raise CausalityError(
                    f"{s.timeframe.value} 봉이 as_of 이후에 마감됨: "
                    f"close_ms={last.close_ms} > as_of_ms={self.as_of_ms}"
                )

    def get(self, timeframe: Timeframe) -> TimeframeSeries | None:
        for s in self.series:
            if s.timeframe is timeframe:
                return s
        return None

    def require(self, timeframe: Timeframe) -> TimeframeSeries:
        s = self.get(timeframe)
        if s is None:
            raise DataIntegrityError(f"스냅샷에 {timeframe.value} 시계열이 없다")
        return s

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of_ms": self.as_of_ms,
            "mark": self.mark_price,
            "funding": self.funding_rate,
            "spread_bps": self.spread_bps,
            "series": sorted(self.series, key=lambda s: s.timeframe.value),
        }

    def digest(self) -> str:
        """입력 해시. ``decisions.snapshot_digest`` 에 저장되어 재현에 쓰인다.

        ``mode`` 는 의도적으로 제외한다. 같은 시장 데이터라면 백테스트와 라이브가
        같은 해시를 내야 일치성(parity)을 검증할 수 있다.
        """
        return sha256_hex(self.to_digest_dict())


# ---------------------------------------------------------------- 계좌 / 포지션


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    client_order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    purpose: OrderPurpose
    qty: Decimal
    state: OrderState
    filled_qty: Decimal = ZERO
    price: Decimal | None = None
    stop_price: Decimal | None = None
    reduce_only: bool = False
    close_position: bool = False
    exchange_order_id: str | None = None
    created_ms: int = 0

    def __post_init__(self) -> None:
        if self.qty < ZERO:
            raise DataIntegrityError(f"주문 수량이 음수: {self.qty}")
        if self.filled_qty < ZERO or self.filled_qty > self.qty:
            raise DataIntegrityError(f"체결 수량이 범위를 벗어남: {self.filled_qty}/{self.qty}")

    @property
    def remaining_qty(self) -> Decimal:
        return self.qty - self.filled_qty

    @property
    def is_active(self) -> bool:
        return self.state.is_active

    @property
    def is_protective(self) -> bool:
        """포지션을 줄이는 방향으로만 동작하는 보호주문인가."""
        return self.purpose.is_protective and (self.reduce_only or self.close_position)


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    symbol: str
    side: PositionSide
    state: PositionState
    qty: Decimal
    entry_price: Decimal | None = None
    opened_ms: int | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    leverage: Decimal | None = None
    liquidation_price: Decimal | None = None
    unrealized_pnl: Decimal = ZERO
    planned_risk: Decimal | None = None

    def __post_init__(self) -> None:
        if self.qty < ZERO:
            raise DataIntegrityError(f"포지션 수량이 음수: {self.qty}. 방향은 side 로 표현한다")
        if self.side is PositionSide.FLAT and self.qty != ZERO:
            raise DataIntegrityError(f"FLAT 인데 수량이 있음: {self.qty}")

    @property
    def is_flat(self) -> bool:
        return self.side is PositionSide.FLAT or self.qty == ZERO

    @property
    def notional(self) -> Decimal:
        return self.qty * self.entry_price if self.entry_price is not None else ZERO

    def holding_ms(self, as_of_ms: int) -> int:
        """보유 시간. 미개시 상태면 0."""
        if self.opened_ms is None:
            return 0
        return max(0, as_of_ms - self.opened_ms)

    def requires_protection(self) -> bool:
        """불변식 INV-1 대상인가 (04-state-machine.md §3)."""
        return self.state.requires_protective_order and not self.is_flat


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """계좌 상태. 거래소에서 읽은 값이 진실의 원천이다 (AGENTS.md §3.3)."""

    equity: Decimal
    wallet_balance: Decimal
    available_balance: Decimal
    equity_high_water: Decimal
    unrealized_pnl: Decimal = ZERO
    position: PositionSnapshot | None = None
    open_orders: tuple[OrderSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if self.equity_high_water < ZERO:
            raise DataIntegrityError(f"equity_high_water 가 음수: {self.equity_high_water}")

    @property
    def is_flat(self) -> bool:
        return self.position is None or self.position.is_flat

    @property
    def drawdown_pct(self) -> Decimal:
        """최고점 대비 낙폭 (0.0 ~ 1.0).

        ``equity_high_water`` 는 DB 에 영속화된 값이어야 한다. 메모리에만 두면
        재시작 시 0으로 리셋되어 DD 한도가 무력화된다 (05-risk-engine.md §4.2).
        """
        if self.equity_high_water <= ZERO:
            return ZERO
        return max(ZERO, safe_div(self.equity_high_water - self.equity, self.equity_high_water))

    @property
    def has_unknown_orders(self) -> bool:
        """해소되지 않은 UNKNOWN 주문이 있는가. 있으면 신규 진입 금지 (G1.3)."""
        return any(o.state.blocks_new_entry for o in self.open_orders)

    @property
    def protective_orders(self) -> tuple[OrderSnapshot, ...]:
        return tuple(o for o in self.open_orders if o.is_protective and o.is_active)


# ---------------------------------------------------------------- 전략 산출


@dataclass(frozen=True, slots=True)
class Component:
    """점수 구성 요소 하나. ``decisions.components_json`` 에 그대로 저장된다."""

    name: str
    layer: str  # "bias" | "trigger"
    raw: float
    normalized: float
    signal: float
    weight: float

    def __post_init__(self) -> None:
        if not -1.0 <= self.signal <= 1.0:
            raise ValueError(f"signal 은 [-1,1] 범위여야 한다: {self.name}={self.signal}")
        if self.layer not in ("bias", "trigger"):
            raise ValueError(f"layer 는 bias 또는 trigger: {self.layer}")

    @property
    def contribution(self) -> float:
        return self.signal * self.weight

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "layer": self.layer,
            "raw": self.raw,
            "norm": self.normalized,
            "sig": self.signal,
            "w": self.weight,
        }


@dataclass(frozen=True, slots=True)
class ScoreSet:
    """점수 묶음.

    ``long_score`` 와 ``short_score`` 는 ``direction`` 에서 파생된다.
    독립 계산하면 "롱 80, 숏 75" 같은 해석 불가능한 상태가 생긴다
    (06-scoring-engine.md §3.1).
    """

    direction: float
    confidence_score: float
    risk_score: float
    bias_direction: int
    bias_multiplier: float
    agreement: float
    regime: str | None = None

    def __post_init__(self) -> None:
        if not -1.0 <= self.direction <= 1.0:
            raise ValueError(f"direction 은 [-1,1]: {self.direction}")
        if self.bias_direction not in (-1, 0, 1):
            raise ValueError(f"bias_direction 은 -1/0/1: {self.bias_direction}")
        for name in ("confidence_score", "risk_score"):
            value: float = getattr(self, name)
            if not 0.0 <= value <= 100.0:
                raise ValueError(f"{name} 은 [0,100]: {value}")
        for name in ("bias_multiplier", "agreement"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 은 [0,1]: {value}")

    @property
    def long_score(self) -> float:
        return 50.0 * (1.0 + self.direction)

    @property
    def short_score(self) -> float:
        return 100.0 - self.long_score

    @classmethod
    def neutral(cls) -> ScoreSet:
        return cls(
            direction=0.0,
            confidence_score=0.0,
            risk_score=0.0,
            bias_direction=0,
            bias_multiplier=0.0,
            agreement=0.0,
        )

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "dir": self.direction,
            "conf": self.confidence_score,
            "risk": self.risk_score,
            "bias": self.bias_direction,
            "bias_mult": self.bias_multiplier,
            "agree": self.agreement,
            "regime": self.regime,
        }


@dataclass(frozen=True, slots=True)
class Intent:
    """주문 의도. 아직 거래소에 나가지 않은 상태.

    ``client_order_id`` 는 결정론적으로 생성되어 재시작 중복 진입을 차단한다
    (04-state-machine.md §1).
    """

    purpose: OrderPurpose
    side: Side
    order_type: OrderType
    qty: Decimal
    client_order_id: str
    price: Decimal | None = None
    stop_price: Decimal | None = None
    reduce_only: bool = False
    close_position: bool = False

    def __post_init__(self) -> None:
        if not self.close_position and self.qty <= ZERO:
            raise ValueError(f"주문 수량이 0 이하: {self.qty}")
        if self.order_type is OrderType.LIMIT and self.price is None:
            raise ValueError("LIMIT 주문에 price 가 없다")
        if self.order_type in (OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET):
            if self.stop_price is None:
                raise ValueError(f"{self.order_type.value} 에 stop_price 가 없다")
            if not (self.reduce_only or self.close_position):
                raise ValueError(
                    f"{self.order_type.value} 는 reduce_only 또는 close_position 이어야 한다 "
                    "(INV-5: 보호주문이 반대 포지션을 열면 안 된다)"
                )

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose.value,
            "side": self.side.value,
            "type": self.order_type.value,
            "qty": self.qty,
            "price": self.price,
            "stop": self.stop_price,
            "reduce_only": self.reduce_only,
            "close_position": self.close_position,
            "coid": self.client_order_id,
        }


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    """Risk Engine 의 판정. 진입을 거부할 수는 있어도 만들 수는 없다."""

    approved: bool
    reasons: tuple[VetoReason, ...] = ()
    size_multiplier: float = 1.0
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.approved and self.reasons:
            raise ValueError("승인인데 거부 사유가 있다")
        if not self.approved and not self.reasons:
            raise ValueError("거부인데 사유가 없다")
        if not 0.0 <= self.size_multiplier <= 1.0:
            raise ValueError(f"size_multiplier 는 [0,1]: {self.size_multiplier}")

    @classmethod
    def approve(cls, size_multiplier: float = 1.0) -> RiskVerdict:
        return cls(approved=True, size_multiplier=size_multiplier)

    @classmethod
    def veto(cls, *reasons: VetoReason) -> RiskVerdict:
        return cls(approved=False, reasons=tuple(reasons), size_multiplier=0.0)


@dataclass(frozen=True, slots=True)
class Decision:
    """한 번의 평가 결과. 주문 여부와 무관하게 **항상** 기록된다 (AGENTS.md §3.7)."""

    as_of_ms: int
    symbol: str
    action: Action
    scores: ScoreSet
    data_quality: DataQuality
    snapshot_digest: str
    veto_reasons: tuple[VetoReason, ...] = ()
    intents: tuple[Intent, ...] = ()
    components: tuple[Component, ...] = ()
    expected_edge_bps: float | None = None
    expected_cost_bps: float | None = None
    required_edge_bps: float | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.veto_reasons and self.intents:
            raise ValueError("거부된 결정에 주문 의도가 있다")
        if self.action is Action.NONE and self.intents:
            raise ValueError("action=NONE 인데 주문 의도가 있다")

    @property
    def is_veto(self) -> bool:
        return bool(self.veto_reasons)

    @property
    def will_trade(self) -> bool:
        return bool(self.intents)

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "as_of_ms": self.as_of_ms,
            "symbol": self.symbol,
            "action": self.action.value,
            "scores": self.scores,
            "vetoes": sorted(r.value for r in self.veto_reasons),
            "intents": list(self.intents),
            "edge": self.expected_edge_bps,
            "cost": self.expected_cost_bps,
            "required": self.required_edge_bps,
        }

    def digest(self) -> str:
        """결정 해시. Parity Test 에서 백테스트와 라이브를 비교하는 기준이다
        (07-backtest.md §6.1)."""
        return sha256_hex(self.to_digest_dict())
