"""결정론적 가짜 USD-M 선물 거래소 — 상태 전이·장애 주입 테스트용 (Phase 5 선물).

one-way 포지션, 교차 지갑, 수수료, reduce-only, closePosition 보호 스톱, funding, 청산을 흉내 낸다.
시장가는 현재 mark 가격에 즉시 체결한다(선택적으로 일부만 체결 후 EXPIRED).
지정가 IOC 는 가격 한도가 mark 를 넘으면(매수 ≥ mark, 매도 ≤ mark) mark 에 체결하고 — 한도보다
좋은 값 — 아니면 체결 없이 EXPIRED 다. 걸어 두는 일반 주문은 남지 않는다.

:meth:`set_mark_price` 가 가격을 움직이면 걸려 있는 스톱을 판정한다. 발동한 스톱은 **새 가격**에
체결된다 — 스톱가를 건너뛴 갭이면 스톱가보다 나쁜 가격이다
(TRADING_CORE: gap 은 손절가 체결 보장이 아님).

청산은 단순화했다: 지갑 + 미실현 손익이 유지증거금(명목 × ``maintenance_rate``) 이하가 되면
모든 포지션을 그 가격에 닫고, 걸린 스톱을 만료시킨다.
실제 거래소의 보험기금·단계별 유지증거금은 없다.

장애 주입(현물 가짜 거래소와 같은 이름):

* ``fail_before_accept`` — 다음 N번 주문을 받기 **전에** 네트워크 오류 (주문 없음)
* ``lose_response_after_accept`` — 다음 N번 주문을 받은 **뒤** 응답 유실 (주문 있음, 결과 모름)
* ``fail_lookups`` — 다음 N번 조회 실패
* ``fail_cancels`` — 다음 N번 취소 요청 실패 (취소 안 됨)
* ``fail_account`` — 다음 N번 계좌 조회 실패
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from auto_trading.core.errors import ExchangeError, OrderRejectedError, OrderStateUnknownError
from auto_trading.core.money import floor_to_step
from auto_trading.exchange.futures import (
    FuturesAccount,
    FuturesFilters,
    FuturesOrderResult,
    FuturesOrderType,
    FuturesPosition,
    OrderStatus,
)

__all__ = ["FakeFuturesExchange"]

_QUOTE = "USDT"
_ZERO = Decimal(0)


def _sign(x: Decimal) -> int:
    return (x > 0) - (x < 0)


@dataclass
class FakeFuturesExchange:
    wallet: Decimal
    prices: dict[str, Decimal]
    symbol_filters: dict[str, FuturesFilters]
    leverage: dict[str, int] = field(default_factory=dict)
    default_leverage: int = 3
    fee_rate: Decimal = Decimal("0.0005")
    maintenance_rate: Decimal = Decimal("0.005")
    fill_ratio: Decimal = Decimal(1)
    fail_before_accept: int = 0
    lose_response_after_accept: int = 0
    fail_lookups: int = 0
    fail_cancels: int = 0
    fail_account: int = 0
    orders: dict[str, FuturesOrderResult] = field(default_factory=dict)
    placed_count: int = 0
    liquidations: int = 0
    _qty: dict[str, Decimal] = field(default_factory=dict)
    _entry: dict[str, Decimal] = field(default_factory=dict)

    # ------------------------------------------------------------ 조회

    def filters(self, symbol: str) -> FuturesFilters:
        return self.symbol_filters[symbol]

    def mark_price(self, symbol: str) -> Decimal:
        return self.prices[symbol]

    def _lev(self, symbol: str) -> int:
        return self.leverage.get(symbol, self.default_leverage)

    def _upnl(self) -> Decimal:
        return sum(
            (q * (self.prices[s] - self._entry[s]) for s, q in self._qty.items() if q),
            _ZERO,
        )

    def _initial_margin(self) -> Decimal:
        return sum(
            (abs(q) * self.prices[s] / self._lev(s) for s, q in self._qty.items() if q), _ZERO
        )

    def account(self) -> FuturesAccount:
        if self.fail_account:
            self.fail_account -= 1
            raise ExchangeError("계좌 조회 실패")
        positions = {
            s: FuturesPosition(s, q, self._entry[s], self._lev(s))
            for s, q in sorted(self._qty.items())
            if q
        }
        available = self.wallet + self._upnl() - self._initial_margin()
        return FuturesAccount(self.wallet, max(available, _ZERO), positions)

    def _lookup(
        self, symbol: str, client_order_id: str, kind: FuturesOrderType
    ) -> FuturesOrderResult | None:
        if self.fail_lookups:
            self.fail_lookups -= 1
            raise ExchangeError("조회 실패")
        order = self.orders.get(client_order_id)
        if order is None or order.symbol != symbol or order.order_type is not kind:
            return None
        return order

    def _working(self, symbol: str, kind: FuturesOrderType) -> list[FuturesOrderResult]:
        return [
            o
            for o in self.orders.values()
            if o.symbol == symbol and o.status is OrderStatus.NEW and o.order_type is kind
        ]

    def get_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        order = self._lookup(symbol, client_order_id, FuturesOrderType.MARKET)
        if order is None and client_order_id in self.orders:
            order = self.orders[client_order_id]
            if order.symbol != symbol or order.order_type is not FuturesOrderType.LIMIT:
                return None
        return order

    def open_orders(self, symbol: str) -> list[FuturesOrderResult]:
        return self._working(symbol, FuturesOrderType.MARKET) + self._working(
            symbol, FuturesOrderType.LIMIT
        )

    def get_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self._lookup(symbol, client_order_id, FuturesOrderType.STOP_MARKET)

    def open_stops(self, symbol: str) -> list[FuturesOrderResult]:
        return self._working(symbol, FuturesOrderType.STOP_MARKET)

    # ------------------------------------------------------------ 주문

    def _accept_or_fail(self, client_order_id: str) -> None:
        if self.fail_before_accept:
            self.fail_before_accept -= 1
            raise ExchangeError("네트워크 오류 (주문 전송 전)")
        if client_order_id in self.orders:
            raise OrderRejectedError(f"Duplicate order sent: {client_order_id}")

    def _record(self, result: FuturesOrderResult) -> FuturesOrderResult:
        self.placed_count += 1
        self.orders[result.client_order_id] = result
        if self.lose_response_after_accept:
            self.lose_response_after_accept -= 1
            raise OrderStateUnknownError("응답 유실 (주문은 접수됨)")
        return result

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_order_id: str,
        *,
        reduce_only: bool,
    ) -> FuturesOrderResult:
        return self._execute(symbol, side, quantity, client_order_id, reduce_only, None)

    def place_limit_ioc(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
        *,
        reduce_only: bool,
    ) -> FuturesOrderResult:
        return self._execute(symbol, side, quantity, client_order_id, reduce_only, price)

    def _execute(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        client_order_id: str,
        reduce_only: bool,
        limit: Decimal | None,
    ) -> FuturesOrderResult:
        """시장가(``limit`` 없음) 또는 지정가 IOC. 체결가는 mark."""
        self._accept_or_fail(client_order_id)
        f = self.symbol_filters[symbol]
        price = self.prices[symbol]
        if quantity != floor_to_step(quantity, f.step_size) or quantity < f.min_qty:
            raise OrderRejectedError("LOT_SIZE")
        if limit is not None and (limit <= 0 or limit != floor_to_step(limit, f.tick_size)):
            raise OrderRejectedError("PRICE_FILTER")
        if not reduce_only and quantity * price < f.min_notional:
            raise OrderRejectedError("MIN_NOTIONAL")
        delta = quantity if side == "BUY" else -quantity
        current = self._qty.get(symbol, _ZERO)
        if reduce_only and (
            current == 0 or _sign(delta) == _sign(current) or quantity > abs(current)
        ):
            raise OrderRejectedError("ReduceOnly Order is rejected")
        increase = max(abs(current + delta) - abs(current), _ZERO)
        if increase:
            need = increase * price / self._lev(symbol) + quantity * price * self.fee_rate
            if need > self.account().available_balance:
                raise OrderRejectedError("Margin is insufficient")
        crosses = limit is None or (limit >= price if side == "BUY" else limit <= price)
        executed = floor_to_step(quantity * self.fill_ratio, f.step_size) if crosses else _ZERO
        fee = executed * price * self.fee_rate
        self._fill(symbol, executed if side == "BUY" else -executed, price)
        self.wallet -= fee
        status = OrderStatus.FILLED if executed == quantity else OrderStatus.EXPIRED
        return self._record(
            FuturesOrderResult(
                client_order_id=client_order_id,
                exchange_order_id=str(self.placed_count + 1),
                symbol=symbol,
                side=side,
                order_type=FuturesOrderType.MARKET if limit is None else FuturesOrderType.LIMIT,
                status=status,
                orig_qty=quantity,
                executed_qty=executed,
                avg_price=price if executed else _ZERO,
                reduce_only=reduce_only,
                close_position=False,
                stop_price=_ZERO,
                fee=fee,
                fee_asset=_QUOTE,
                limit_price=_ZERO if limit is None else limit,
            )
        )

    def place_stop_market(
        self,
        symbol: str,
        side: str,
        stop_price: Decimal,
        client_order_id: str,
        *,
        quantity: Decimal | None = None,
    ) -> FuturesOrderResult:
        self._accept_or_fail(client_order_id)
        f = self.symbol_filters[symbol]
        if stop_price <= 0 or stop_price != floor_to_step(stop_price, f.tick_size):
            raise OrderRejectedError("PRICE_FILTER")
        if quantity is not None and (
            quantity < f.min_qty or quantity != floor_to_step(quantity, f.step_size)
        ):
            raise OrderRejectedError("LOT_SIZE")
        # 테스트넷 실측(2026-09-24) 규칙과 검사 순서: 닫을 포지션(-4509) → 같은 방향 중복(-4130)
        # → 즉시 발동(-2021). closePosition 스톱은 그 방향으로 닫을 포지션이 있어야 걸린다.
        # 수량 지정 reduce-only 스톱은 closePosition·다른 수량 스톱과 함께 걸린다(2026-09-25 실측).
        position = self._qty.get(symbol, _ZERO)
        if (side == "SELL" and position <= 0) or (side == "BUY" and position >= 0):
            raise OrderRejectedError("-4509 closePosition stop needs an open position to close")
        if quantity is None and any(
            o.side == side and o.close_position for o in self.open_stops(symbol)
        ):
            raise OrderRejectedError("-4130 closePosition stop in this direction already exists")
        mark = self.prices[symbol]
        if (side == "SELL" and stop_price >= mark) or (side == "BUY" and stop_price <= mark):
            raise OrderRejectedError("-2021 Order would immediately trigger")
        return self._record(
            FuturesOrderResult(
                client_order_id=client_order_id,
                exchange_order_id=str(self.placed_count + 1),
                symbol=symbol,
                side=side,
                order_type=FuturesOrderType.STOP_MARKET,
                status=OrderStatus.NEW,
                orig_qty=_ZERO if quantity is None else quantity,
                executed_qty=_ZERO,
                avg_price=_ZERO,
                reduce_only=True,
                close_position=quantity is None,
                stop_price=stop_price,
                fee=_ZERO,
                fee_asset=_QUOTE,
            )
        )

    def cancel_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self._cancel(symbol, client_order_id, FuturesOrderType.MARKET)

    def cancel_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self._cancel(symbol, client_order_id, FuturesOrderType.STOP_MARKET)

    def _cancel(
        self, symbol: str, client_order_id: str, kind: FuturesOrderType
    ) -> FuturesOrderResult | None:
        if self.fail_cancels:
            self.fail_cancels -= 1
            raise ExchangeError("취소 요청 실패")
        order = self.orders.get(client_order_id)
        if order is None or order.symbol != symbol or order.order_type is not kind:
            return None
        if order.status is not OrderStatus.NEW:
            raise OrderRejectedError(f"Unknown order sent (이미 {order.status})")
        canceled = replace(order, status=OrderStatus.CANCELED)
        self.orders[client_order_id] = canceled
        return canceled

    # ------------------------------------------------------------ 시장 움직임

    def set_mark_price(self, symbol: str, price: Decimal) -> list[FuturesOrderResult]:
        """가격을 옮기고 걸린 스톱을 판정한다. 발동·만료된 스톱을 돌려준다."""
        self.prices[symbol] = price
        changed: list[FuturesOrderResult] = []
        for order in list(self.orders.values()):
            if order.symbol != symbol or order.status is not OrderStatus.NEW:
                continue
            if order.order_type is not FuturesOrderType.STOP_MARKET:
                continue
            hit = price <= order.stop_price if order.side == "SELL" else price >= order.stop_price
            if not hit:
                continue
            position = self._qty.get(symbol, _ZERO)
            closes = (order.side == "SELL" and position > 0) or (
                order.side == "BUY" and position < 0
            )
            if not closes:
                done = replace(order, status=OrderStatus.EXPIRED)
            else:
                # closePosition 은 전량, 수량 스톱은 reduce-only 라 포지션을 넘지 않는다
                qty = abs(position) if order.close_position else min(order.orig_qty, abs(position))
                fee = qty * price * self.fee_rate
                self._fill(symbol, -qty if position > 0 else qty, price)
                self.wallet -= fee
                done = replace(
                    order, status=OrderStatus.FILLED, executed_qty=qty, avg_price=price, fee=fee
                )
            self.orders[order.client_order_id] = done
            changed.append(done)
        changed += self._check_liquidation()
        return changed

    def settle_funding(self, symbol: str, rate: Decimal) -> Decimal:
        """funding 정산. 롱은 양수 비율에서 낸다. 지갑 변화량(낸 쪽 음수)을 돌려준다."""
        qty = self._qty.get(symbol, _ZERO)
        paid = qty * self.prices[symbol] * rate
        self.wallet -= paid
        return -paid

    # ------------------------------------------------------------ 내부

    def _fill(self, symbol: str, signed: Decimal, price: Decimal) -> None:
        if not signed:
            return
        current = self._qty.get(symbol, _ZERO)
        entry = self._entry.get(symbol, _ZERO)
        new = current + signed
        if current == 0 or _sign(signed) == _sign(current):
            entry = (abs(current) * entry + abs(signed) * price) / abs(new)
        else:
            closing = min(abs(signed), abs(current))
            self.wallet += closing * (price - entry) * _sign(current)
            if new == 0:
                entry = _ZERO
            elif _sign(new) != _sign(current):
                entry = price  # 방향이 뒤집힌 나머지는 새 진입
        self._qty[symbol] = new
        self._entry[symbol] = entry
        if new == 0:
            self._expire_close_stops(symbol)

    def _expire_close_stops(self, symbol: str) -> None:
        """포지션이 0 이 되면 남은 closePosition 스톱은 할 일이 없다."""
        for cid, order in list(self.orders.items()):
            if order.symbol == symbol and order.status is OrderStatus.NEW and order.close_position:
                self.orders[cid] = replace(order, status=OrderStatus.EXPIRED)

    def _check_liquidation(self) -> list[FuturesOrderResult]:
        open_positions = {s: q for s, q in self._qty.items() if q}
        if not open_positions:
            return []
        equity = self.wallet + self._upnl()
        maintenance = sum(
            (abs(q) * self.prices[s] * self.maintenance_rate for s, q in open_positions.items()),
            _ZERO,
        )
        if equity > maintenance:
            return []
        self.liquidations += 1
        self.wallet = max(equity, _ZERO)
        for s in open_positions:
            self._qty[s] = _ZERO
            self._entry[s] = _ZERO
        expired = []
        for cid, order in list(self.orders.items()):
            if order.status is OrderStatus.NEW:
                self.orders[cid] = replace(order, status=OrderStatus.EXPIRED)
                expired.append(self.orders[cid])
        return expired
