"""데모 현물 지정가 L240 체결기 — 가짜 거래소로 검증 (55)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from auto_trading.core.errors import OrderRejectedError
from auto_trading.exchange.binance_spot_demo import SpotFill
from auto_trading.exchange.spot import OrderStatus, SpotOrderResult
from auto_trading.execution.demo_limit import DemoLimitOrder, run_limit_orders

BID, ASK = Decimal("100.0"), Decimal("100.2")


@dataclass
class FakeVenue:
    """지정가는 ``fill_after`` 번 조회 뒤 ``fill_frac`` 만큼 체결된다."""

    fill_after: int = 1
    fill_frac: Decimal = Decimal(1)
    reject_posts: int = 0
    orders: dict[str, SpotOrderResult] = field(default_factory=dict)
    prices: dict[str, Decimal] = field(default_factory=dict)
    polls: int = 0
    placed: list[str] = field(default_factory=list)

    def order_book(self, symbol: str, limit: int = 100):
        return [(BID, Decimal(1))], [(ASK, Decimal(1))]

    def _new(self, cid: str, side: str, qty: Decimal, status: OrderStatus, executed: Decimal):
        r = SpotOrderResult(
            cid,
            str(len(self.orders) + 1),
            "BTCUSDT",
            side,
            status,
            qty,
            executed,
            Decimal(0),
            Decimal(0),
            "",
        )
        self.orders[cid] = r
        return r

    def place_limit_maker(self, symbol, side, quantity, price, client_order_id):
        if self.reject_posts > 0:
            self.reject_posts -= 1
            raise OrderRejectedError("would match")
        self.placed.append(client_order_id)
        self.prices[client_order_id] = price
        return self._new(client_order_id, side, quantity, OrderStatus.NEW, Decimal(0))

    def place_market_order(self, symbol, side, quantity, client_order_id):
        self.placed.append(client_order_id)
        self.prices[client_order_id] = ASK if side == "BUY" else BID
        return self._new(client_order_id, side, quantity, OrderStatus.FILLED, quantity)

    def cancel_order(self, symbol, client_order_id):
        r = self.orders[client_order_id]
        self.orders[client_order_id] = replace(r, status=OrderStatus.CANCELED)
        return self.orders[client_order_id]

    def get_order(self, symbol, client_order_id):
        r = self.orders.get(client_order_id)
        if r is None or r.status is not OrderStatus.NEW:
            return r
        self.polls += 1
        if self.polls >= self.fill_after:
            executed = r.orig_qty * self.fill_frac
            status = OrderStatus.FILLED if self.fill_frac == 1 else OrderStatus.PARTIALLY_FILLED
            r = replace(r, status=status, executed_qty=executed)
            self.orders[client_order_id] = r
        return r

    def fills(self, symbol, exchange_order_id):
        (cid,) = [c for c, r in self.orders.items() if r.exchange_order_id == exchange_order_id]
        r = self.orders[cid]
        price = self.prices[cid]
        maker = not cid.endswith("m")
        fee = r.executed_qty * Decimal("0.001")  # 매수 수수료는 기초 자산으로
        return [SpotFill(price, r.executed_qty, r.executed_qty * price, fee, "BTC", maker)]


class Clock:
    def __init__(self) -> None:
        self.ms = 0

    def __call__(self) -> int:
        return self.ms

    def sleep(self, s: float) -> None:
        self.ms += int(s * 1000)


ORDER = DemoLimitOrder("BTCUSDT", "BUY", Decimal("1"), "dt2-x-B", Decimal("0.001"), Decimal(5))


def run(venue: FakeVenue, **kw):
    clock = Clock()
    events: list[str] = []
    (out,) = run_limit_orders(
        venue,
        [ORDER],
        clock=clock,
        sleep=clock.sleep,
        on_event=lambda k, _o: events.append(k),
        **kw,
    )
    return out, events


def test_maker_fill_at_bid_is_cheaper_than_mid() -> None:
    out, events = run(FakeVenue(fill_after=2))
    assert events == ["posted", "filled"]
    assert out.limit_price == BID and out.maker_qty == 1 and not out.chased
    price_bps, fee_bps = out.is_bps()
    assert price_bps < 0  # 매수 1호가 < 중간가
    assert fee_bps == Decimal(10)  # 0.1%
    assert out.wait_min == 1.0


def test_unfilled_remainder_is_chased_with_market_after_timeout() -> None:
    out, events = run(FakeVenue(fill_after=1, fill_frac=Decimal("0.4")), timeout_min=10)
    assert events == ["posted", "chased"]
    assert out.maker_qty == Decimal("0.4") and out.taker_qty == Decimal("0.6")
    assert out.chased
    assert out.wait_min == 10.0


def test_post_only_rejections_retry_then_fall_back_to_market() -> None:
    out, events = run(FakeVenue(reject_posts=3))
    assert events == ["chased"]
    assert out.post_rejects == 3 and out.taker_qty == 1
    out, events = run(FakeVenue(reject_posts=1))
    assert events == ["posted", "filled"] and out.post_rejects == 1


def test_existing_order_is_resumed_not_resent() -> None:
    venue = FakeVenue(fill_after=1)
    venue.place_limit_maker("BTCUSDT", "BUY", Decimal(1), BID, ORDER.client_id)
    venue.polls = -5  # 이어받은 뒤 몇 번 더 조회해야 체결
    out, _ = run(venue)
    assert venue.placed == [ORDER.client_id]  # 다시 보내지 않았다
    assert "이어받음" in out.notes[0]
