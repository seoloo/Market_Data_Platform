"""현물 거래소 계약 (T/v2 Phase 5). 구현: FakeSpotExchange(테스트·Paper), Binance 어댑터(이후).

오류 약속:

* :class:`OrderRejectedError` — 거래소가 주문을 **받지 않았다**(필터·잔고·중복 ID). 재시도해도 같다.
* :class:`OrderStateUnknownError` — 요청은 나갔지만 결과를 모른다(응답 유실·타임아웃).
* 그 밖의 :class:`ExchangeError` — 네트워크 등. 주문이 들어갔는지 모르므로 UNKNOWN으로 다룬다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

__all__ = ["OrderStatus", "SpotExchange", "SpotFilters", "SpotOrderResult"]


class OrderStatus(StrEnum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class SpotFilters:
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass(frozen=True, slots=True)
class SpotOrderResult:
    client_order_id: str
    exchange_order_id: str
    symbol: str
    side: str
    status: OrderStatus
    orig_qty: Decimal
    executed_qty: Decimal
    quote_qty: Decimal
    """체결 명목(수수료 제외)."""
    fee: Decimal
    fee_asset: str


class SpotExchange(Protocol):
    def filters(self, symbol: str) -> SpotFilters: ...

    def balances(self) -> dict[str, Decimal]: ...

    def last_price(self, symbol: str) -> Decimal: ...

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> SpotOrderResult: ...

    def get_order(self, symbol: str, client_order_id: str) -> SpotOrderResult | None:
        """``client_order_id``로 조회. 거래소에 없으면 None (주문이 들어가지 않았다)."""
        ...
