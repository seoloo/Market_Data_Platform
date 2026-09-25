"""데모 현물 지정가 L240 체결기 (docs/research/42 §7.6 → 55).

한 날의 주문 의도 여러 개를 **동시에** 처리한다.

1. 주문 순간 호가를 읽어 중간가 P0 를 기록하고, 매수는 매수 1호가·매도는 매도 1호가에
   post-only(LIMIT_MAKER)로 건다. post-only 가 거절되면(가격이 움직여 바로 체결될 가격)
   호가를 다시 읽어 최대 3 번 다시 건다. 그래도 안 되면 시장가.
2. ``poll_s`` 마다 주문을 조회한다. 전부 체결되면 끝.
3. 건 뒤 ``timeout_min`` 분이 지나면 취소하고, 남은 수량을 시장가로 추격한다.
4. 체결(myTrades)로 평균가·maker/taker 수량·수수료(USDT 환산)를 모아
   **P0 대비 비용(IS, bp)** 을 낸다.

멱등성: clientOrderId 는 (결정일·종목·방향·목적)으로 정해진다.
걸기 전에 같은 ID 를 조회하고, 있으면 다시 보내지 않고 이어받는다.
거래소·시계·대기는 주입한다(테스트는 가짜 거래소로).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from auto_trading.core.errors import OrderRejectedError
from auto_trading.core.money import floor_to_step
from auto_trading.exchange.binance_spot_demo import SpotFill
from auto_trading.exchange.spot import OrderStatus, SpotOrderResult

__all__ = ["DemoLimitOrder", "DemoSpotVenue", "LimitOutcome", "run_limit_orders"]

_BPS = Decimal(10_000)
_TERMINAL = frozenset({OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED})
_POST_RETRIES = 3


class DemoSpotVenue(Protocol):
    def order_book(
        self, symbol: str, limit: int = 100
    ) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]: ...

    def place_limit_maker(
        self, symbol: str, side: str, quantity: Decimal, price: Decimal, client_order_id: str
    ) -> SpotOrderResult: ...

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> SpotOrderResult: ...

    def cancel_order(self, symbol: str, client_order_id: str) -> SpotOrderResult: ...

    def get_order(self, symbol: str, client_order_id: str) -> SpotOrderResult | None: ...

    def fills(self, symbol: str, exchange_order_id: str) -> list[SpotFill]: ...


@dataclass(frozen=True, slots=True)
class DemoLimitOrder:
    symbol: str
    side: str
    """BUY | SELL"""
    quantity: Decimal
    client_id: str
    """지정가 주문 ID. 추격 시장가는 ``client_id + 'm'``."""
    step_size: Decimal
    min_notional: Decimal


@dataclass(slots=True)
class LimitOutcome:
    order: DemoLimitOrder
    p0_mid: Decimal = Decimal(0)
    limit_price: Decimal | None = None
    posted_ms: int = 0
    filled_ms: int | None = None
    maker_qty: Decimal = Decimal(0)
    taker_qty: Decimal = Decimal(0)
    quote: Decimal = Decimal(0)
    fee_usdt: Decimal = Decimal(0)
    post_rejects: int = 0
    chased: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def filled_qty(self) -> Decimal:
        return self.maker_qty + self.taker_qty

    @property
    def avg_price(self) -> Decimal | None:
        return self.quote / self.filled_qty if self.filled_qty else None

    @property
    def wait_min(self) -> float | None:
        if self.filled_ms is None:
            return None
        return (self.filled_ms - self.posted_ms) / 60_000

    def is_bps(self) -> tuple[Decimal, Decimal] | None:
        """(가격 부분, 수수료 부분) bp — P0(주문 순간 중간가) 대비. 방향: 매수 +, 매도 −."""
        avg = self.avg_price
        if avg is None or not self.p0_mid:
            return None
        sign = 1 if self.order.side == "BUY" else -1
        price_part = sign * (avg - self.p0_mid) / self.p0_mid * _BPS
        fee_part = self.fee_usdt / self.quote * _BPS
        return price_part, fee_part


Event = Callable[[str, LimitOutcome], None]
"""("posted" | "filled" | "chased" | "failed", 결과) — 알림용."""


def _mid(venue: DemoSpotVenue, symbol: str) -> tuple[Decimal, Decimal, Decimal]:
    bids, asks = venue.order_book(symbol, limit=5)
    bid, ask = bids[0][0], asks[0][0]
    return bid, ask, (bid + ask) / 2


def _post(venue: DemoSpotVenue, out: LimitOutcome, clock: Callable[[], int]) -> bool:
    """post-only 를 건다. 이미 있으면 이어받는다. 걸었으면 True."""
    o = out.order
    existing = venue.get_order(o.symbol, o.client_id)
    bid, ask, mid = _mid(venue, o.symbol)
    if existing is not None:
        out.p0_mid, out.posted_ms = mid, clock()
        out.notes.append("이미 걸린 주문을 이어받음")
        return True
    for _ in range(_POST_RETRIES):
        price = bid if o.side == "BUY" else ask
        try:
            venue.place_limit_maker(o.symbol, o.side, o.quantity, price, o.client_id)
        except OrderRejectedError:
            out.post_rejects += 1
            bid, ask, mid = _mid(venue, o.symbol)
            continue
        out.p0_mid, out.limit_price, out.posted_ms = mid, price, clock()
        return True
    out.p0_mid, out.posted_ms = mid, clock()
    return False


def _collect(venue: DemoSpotVenue, out: LimitOutcome, result: SpotOrderResult) -> None:
    for f in venue.fills(out.order.symbol, result.exchange_order_id):
        if f.is_maker:
            out.maker_qty += f.qty
        else:
            out.taker_qty += f.qty
        out.quote += f.quote_qty
        if f.commission_asset == "USDT":
            out.fee_usdt += f.commission
        elif out.order.symbol.startswith(f.commission_asset):
            out.fee_usdt += f.commission * f.price
        else:
            out.notes.append(f"수수료 자산 {f.commission_asset} {f.commission} — USDT 환산 안 함")


def _chase(venue: DemoSpotVenue, out: LimitOutcome, remaining: Decimal) -> None:
    o = out.order
    qty = floor_to_step(remaining, o.step_size)
    _, _, mid = _mid(venue, o.symbol)
    if qty <= 0 or qty * mid < o.min_notional:
        out.notes.append(f"남은 수량 {remaining} 이 최소 주문 미만 — 추격 안 함")
        return
    cid = o.client_id + "m"
    result = venue.get_order(o.symbol, cid) or venue.place_market_order(o.symbol, o.side, qty, cid)
    if result.status is not OrderStatus.FILLED:
        result = venue.get_order(o.symbol, cid) or result
    _collect(venue, out, result)
    out.chased = True


def run_limit_orders(
    venue: DemoSpotVenue,
    orders: Sequence[DemoLimitOrder],
    *,
    clock: Callable[[], int],
    sleep: Callable[[float], None],
    timeout_min: int = 240,
    poll_s: float = 30.0,
    on_event: Event | None = None,
) -> list[LimitOutcome]:
    emit = on_event or (lambda _kind, _out: None)
    outcomes = [LimitOutcome(order=o) for o in orders]
    open_: list[LimitOutcome] = []
    for out in outcomes:
        if _post(venue, out, clock):
            emit("posted", out)
            open_.append(out)
        else:
            out.notes.append(f"post-only 가 {out.post_rejects} 번 거절 — 바로 시장가")
            _chase(venue, out, out.order.quantity)
            out.filled_ms = clock()
            emit("chased", out)
    while open_:
        sleep(poll_s)
        now = clock()
        still: list[LimitOutcome] = []
        for out in open_:
            o = out.order
            result = venue.get_order(o.symbol, o.client_id)
            if result is None:
                out.notes.append("조회에 주문이 없음 — 추격")
                _chase(venue, out, o.quantity)
                out.filled_ms = now
                emit("chased", out)
                continue
            if result.status is OrderStatus.FILLED:
                _collect(venue, out, result)
                out.filled_ms = now
                emit("filled", out)
                continue
            expired = now - out.posted_ms >= timeout_min * 60_000
            if not expired and result.status not in _TERMINAL:
                still.append(out)
                continue
            if result.status not in _TERMINAL:
                with contextlib.suppress(OrderRejectedError):  # 방금 체결됐을 수 있다 — 조회로 확인
                    venue.cancel_order(o.symbol, o.client_id)
                result = venue.get_order(o.symbol, o.client_id) or result
            if result.executed_qty:
                _collect(venue, out, result)
            remaining = o.quantity - result.executed_qty
            if remaining > 0:
                _chase(venue, out, remaining)
            out.filled_ms = now
            emit("chased" if out.chased else "filled", out)
        open_ = still
    return outcomes
