"""FakeSpotExchange 상태 전이와 장애 주입."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.errors import ExchangeError, OrderRejectedError, OrderStateUnknownError
from auto_trading.exchange.fake_spot import FakeSpotExchange
from auto_trading.exchange.spot import OrderStatus, SpotFilters

FILTERS = {"BTCUSDT": SpotFilters(Decimal("0.00001"), Decimal("0.00001"), Decimal(5))}


def fake(**kwargs: object) -> FakeSpotExchange:
    return FakeSpotExchange(
        balance={"USDT": Decimal(10_000)},
        prices={"BTCUSDT": Decimal(50_000)},
        symbol_filters=FILTERS,
        **kwargs,  # type: ignore[arg-type]
    )


def test_market_buy_fills_and_charges_fee_in_quote() -> None:
    ex = fake()
    order = ex.place_market_order("BTCUSDT", "BUY", Decimal("0.1"), "c1")
    assert order.status is OrderStatus.FILLED
    assert order.executed_qty == Decimal("0.1")
    assert ex.balance["BTC"] == Decimal("0.1")
    assert ex.balance["USDT"] == Decimal(10_000) - Decimal(5000) - Decimal(5)
    assert ex.get_order("BTCUSDT", "c1") == order


def test_sell_returns_quote_minus_fee() -> None:
    ex = fake()
    ex.place_market_order("BTCUSDT", "BUY", Decimal("0.1"), "c1")
    ex.place_market_order("BTCUSDT", "SELL", Decimal("0.1"), "c2")
    assert ex.balance["BTC"] == 0
    assert ex.balance["USDT"] == Decimal(10_000) - Decimal(10)


@pytest.mark.parametrize(
    ("qty", "reason"),
    [
        (Decimal("0.000015"), "LOT_SIZE"),  # step 위반
        (Decimal("0.00005"), "NOTIONAL"),  # 2.5 USDT < 5
        (Decimal("1"), "잔고"),  # 50,000 USDT 필요
    ],
)
def test_rejections_do_not_create_orders(qty: Decimal, reason: str) -> None:
    ex = fake()
    with pytest.raises(OrderRejectedError, match=reason):
        ex.place_market_order("BTCUSDT", "BUY", qty, "c1")
    assert ex.placed_count == 0 and ex.get_order("BTCUSDT", "c1") is None


def test_duplicate_client_order_id_is_rejected() -> None:
    ex = fake()
    ex.place_market_order("BTCUSDT", "BUY", Decimal("0.01"), "c1")
    with pytest.raises(OrderRejectedError, match="Duplicate"):
        ex.place_market_order("BTCUSDT", "BUY", Decimal("0.01"), "c1")
    assert ex.placed_count == 1


def test_partial_fill_expires() -> None:
    ex = fake(fill_ratio=Decimal("0.5"))
    order = ex.place_market_order("BTCUSDT", "BUY", Decimal("0.1"), "c1")
    assert order.status is OrderStatus.EXPIRED and order.executed_qty == Decimal("0.05")


def test_fault_injection() -> None:
    ex = fake(fail_before_accept=1, lose_response_after_accept=1, fail_lookups=1)
    with pytest.raises(ExchangeError):
        ex.place_market_order("BTCUSDT", "BUY", Decimal("0.01"), "c1")
    assert ex.placed_count == 0
    with pytest.raises(OrderStateUnknownError):
        ex.place_market_order("BTCUSDT", "BUY", Decimal("0.01"), "c2")
    assert ex.placed_count == 1
    with pytest.raises(ExchangeError):
        ex.get_order("BTCUSDT", "c2")
    assert ex.get_order("BTCUSDT", "c2") is not None
