"""결정론적 가짜 현물 거래소 — 상태 전이·장애 주입 테스트와 Paper 가상 체결용.

시장가 주문은 설정된 가격에 즉시 체결한다(선택적으로 일부만 체결 후 EXPIRED).
수수료는 인용 자산(USDT)으로 뗀다. 장애 주입:

* ``fail_before_accept`` — 다음 N번 주문을 받기 **전에** 네트워크 오류 (주문 없음)
* ``lose_response_after_accept`` — 다음 N번 주문을 받은 **뒤** 응답 유실 (주문 있음, 결과 모름)
* ``fail_lookups`` — 다음 N번 조회 실패
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from auto_trading.core.errors import ExchangeError, OrderRejectedError, OrderStateUnknownError
from auto_trading.core.money import floor_to_step
from auto_trading.exchange.spot import OrderStatus, SpotFilters, SpotOrderResult

__all__ = ["FakeSpotExchange"]

_QUOTE = "USDT"


@dataclass
class FakeSpotExchange:
    balance: dict[str, Decimal]
    prices: dict[str, Decimal]
    symbol_filters: dict[str, SpotFilters]
    fee_rate: Decimal = Decimal("0.001")
    fill_ratio: Decimal = Decimal(1)
    fail_before_accept: int = 0
    lose_response_after_accept: int = 0
    fail_lookups: int = 0
    orders: dict[str, SpotOrderResult] = field(default_factory=dict)
    placed_count: int = 0

    def filters(self, symbol: str) -> SpotFilters:
        return self.symbol_filters[symbol]

    def balances(self) -> dict[str, Decimal]:
        return dict(self.balance)

    def last_price(self, symbol: str) -> Decimal:
        return self.prices[symbol]

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        if self.fail_before_accept:
            self.fail_before_accept -= 1
            raise ExchangeError("네트워크 오류 (주문 전송 전)")
        if client_order_id in self.orders:
            raise OrderRejectedError(f"Duplicate order sent: {client_order_id}")
        f = self.symbol_filters[symbol]
        price = self.prices[symbol]
        if quantity != floor_to_step(quantity, f.step_size) or quantity < f.min_qty:
            raise OrderRejectedError("LOT_SIZE")
        if quantity * price < f.min_notional:
            raise OrderRejectedError("NOTIONAL")
        base = symbol.removesuffix(_QUOTE)
        executed = floor_to_step(quantity * self.fill_ratio, f.step_size)
        quote = executed * price
        fee = quote * self.fee_rate
        if side == "BUY":
            if self.balance.get(_QUOTE, Decimal(0)) < quantity * price * (1 + self.fee_rate):
                raise OrderRejectedError("잔고 부족")
            self.balance[_QUOTE] -= quote + fee
            self.balance[base] = self.balance.get(base, Decimal(0)) + executed
        else:
            if self.balance.get(base, Decimal(0)) < quantity:
                raise OrderRejectedError("잔고 부족")
            self.balance[base] -= executed
            self.balance[_QUOTE] = self.balance.get(_QUOTE, Decimal(0)) + quote - fee
        self.placed_count += 1
        status = OrderStatus.FILLED if executed == quantity else OrderStatus.EXPIRED
        result = SpotOrderResult(
            client_order_id,
            str(self.placed_count),
            symbol,
            side,
            status,
            quantity,
            executed,
            quote,
            fee,
            _QUOTE,
        )
        self.orders[client_order_id] = result
        if self.lose_response_after_accept:
            self.lose_response_after_accept -= 1
            raise OrderStateUnknownError("응답 유실 (주문은 접수됨)")
        return result

    def get_order(self, symbol: str, client_order_id: str) -> SpotOrderResult | None:
        if self.fail_lookups:
            self.fail_lookups -= 1
            raise ExchangeError("조회 실패")
        order = self.orders.get(client_order_id)
        return order if order is not None and order.symbol == symbol else None
