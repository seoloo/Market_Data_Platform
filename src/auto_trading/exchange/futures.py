"""USD-M 무기한 선물 거래소 계약 (Phase 5 선물). 구현: FakeFuturesExchange(테스트), Binance(이후).

현물 계약(:mod:`auto_trading.exchange.spot`)과 오류 약속이 같다.

* :class:`OrderRejectedError` — 거래소가 주문을 **받지 않았다**
  (필터·증거금·reduce-only 위반·중복 ID).
* :class:`OrderStateUnknownError` — 요청은 나갔지만 결과를 모른다(응답 유실·타임아웃).
* 그 밖의 :class:`ExchangeError` — 네트워크 등. 주문이 들어갔는지 모르므로 UNKNOWN으로 다룬다.

선물 고유 약속:

* 포지션은 **one-way** 모드다. 종목당 부호 있는 수량 하나(+ 롱, − 숏).
* ``reduce_only`` 주문은 포지션을 줄이기만 한다. 늘리거나 방향을 뒤집으면 거절된다.
* 보호 스톱은 ``STOP_MARKET`` · ``closePosition=true`` · ``workingType=MARK_PRICE`` 다
  (TRADING_CORE §포지션 상태·보호주문). 발동 즉시 체결되지만 **손절가 체결을 보장하지 않는다** —
  갭이면 그보다 나쁜 가격에 체결된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from auto_trading.exchange.spot import OrderStatus

__all__ = [
    "FuturesAccount",
    "FuturesExchange",
    "FuturesFilters",
    "FuturesOrderResult",
    "FuturesOrderType",
    "FuturesPosition",
    "OrderStatus",
]


class FuturesOrderType(StrEnum):
    MARKET = "MARKET"
    STOP_MARKET = "STOP_MARKET"


@dataclass(frozen=True, slots=True)
class FuturesFilters:
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    tick_size: Decimal


@dataclass(frozen=True, slots=True)
class FuturesOrderResult:
    client_order_id: str
    exchange_order_id: str
    symbol: str
    side: str
    order_type: FuturesOrderType
    status: OrderStatus
    orig_qty: Decimal
    """시장가 주문의 요청 수량. closePosition 스톱은 0 (발동 시 포지션 전량)."""
    executed_qty: Decimal
    avg_price: Decimal
    """체결 평균가. 미체결이면 0."""
    reduce_only: bool
    close_position: bool
    stop_price: Decimal
    """STOP_MARKET 의 발동가. 시장가 주문은 0."""
    fee: Decimal
    fee_asset: str


@dataclass(frozen=True, slots=True)
class FuturesPosition:
    symbol: str
    quantity: Decimal
    """부호 있는 수량 (+ 롱, − 숏). 0 이면 포지션 없음."""
    entry_price: Decimal
    leverage: int


@dataclass(frozen=True, slots=True)
class FuturesAccount:
    wallet_balance: Decimal
    """실현 손익·수수료·funding 이 반영된 지갑 잔고 (USDT)."""
    available_balance: Decimal
    """새 포지션 증거금으로 쓸 수 있는 금액."""
    positions: dict[str, FuturesPosition]
    """수량이 0 이 아닌 포지션만."""


class FuturesExchange(Protocol):
    def filters(self, symbol: str) -> FuturesFilters: ...

    def account(self) -> FuturesAccount: ...

    def mark_price(self, symbol: str) -> Decimal: ...

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_order_id: str,
        *,
        reduce_only: bool,
    ) -> FuturesOrderResult: ...

    def place_stop_market(
        self, symbol: str, side: str, stop_price: Decimal, client_order_id: str
    ) -> FuturesOrderResult:
        """closePosition 보호 스톱을 건다. 발동 전까지 상태는 NEW 다."""
        ...

    def cancel_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        """걸려 있는 주문을 취소한다. 거래소에 없으면 None. 이미 종결됐으면 OrderRejectedError."""
        ...

    def get_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        """``client_order_id``로 조회. 거래소에 없으면 None (주문이 들어가지 않았다)."""
        ...

    def open_orders(self, symbol: str) -> list[FuturesOrderResult]: ...
