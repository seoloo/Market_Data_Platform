"""가짜 USD-M 선물 거래소: 포지션 회계·reduce-only·증거금·보호 스톱·funding·청산.

이 가짜가 틀리면 그 위의 주문 관리자 테스트가 전부 거짓이 된다. 손으로 계산한 값으로 고정한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.errors import ExchangeError, OrderRejectedError, OrderStateUnknownError
from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import FuturesFilters, FuturesOrderType, OrderStatus

FILTERS = {
    "BTCUSDT": FuturesFilters(Decimal("0.001"), Decimal("0.001"), Decimal(100), Decimal("0.1"))
}


def fake(**kwargs: object) -> FakeFuturesExchange:
    """수수료 0 이 기본이다 — 손익 계산을 손으로 맞추기 쉽게. 수수료 검사는 따로 한다."""
    kwargs.setdefault("fee_rate", Decimal(0))
    return FakeFuturesExchange(
        wallet=Decimal(10_000),
        prices={"BTCUSDT": Decimal(50_000)},
        symbol_filters=FILTERS,
        **kwargs,  # type: ignore[arg-type]
    )


def market(ex: FakeFuturesExchange, side: str, qty: str, cid: str, *, reduce: bool = False):
    return ex.place_market_order("BTCUSDT", side, Decimal(qty), cid, reduce_only=reduce)


class TestPositions:
    def test_open_long_sets_quantity_and_entry(self) -> None:
        ex = fake()
        r = market(ex, "BUY", "0.1", "a")
        assert r.status is OrderStatus.FILLED and r.avg_price == Decimal(50_000)
        pos = ex.account().positions["BTCUSDT"]
        assert pos.quantity == Decimal("0.1") and pos.entry_price == Decimal(50_000)

    def test_adding_averages_the_entry(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        ex.set_mark_price("BTCUSDT", Decimal(60_000))
        market(ex, "BUY", "0.1", "b")
        assert ex.account().positions["BTCUSDT"].entry_price == Decimal(55_000)

    def test_reducing_realizes_pnl_into_the_wallet(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.2", "a")
        ex.set_mark_price("BTCUSDT", Decimal(51_000))
        market(ex, "SELL", "0.1", "b", reduce=True)
        # 0.1 × (51,000 − 50,000) = 100 실현
        assert ex.wallet == Decimal(10_100)
        assert ex.account().positions["BTCUSDT"].quantity == Decimal("0.1")

    def test_short_profits_when_price_falls(self) -> None:
        ex = fake()
        market(ex, "SELL", "0.1", "a")
        ex.set_mark_price("BTCUSDT", Decimal(49_000))
        market(ex, "BUY", "0.1", "b", reduce=True)
        assert ex.wallet == Decimal(10_100)
        assert ex.account().positions == {}

    def test_fee_is_charged_on_notional(self) -> None:
        ex = fake(fee_rate=Decimal("0.0005"))
        market(ex, "BUY", "0.1", "a")
        assert ex.wallet == Decimal(10_000) - Decimal("2.5")  # 5,000 × 0.05%

    def test_partial_fill_expires_the_rest(self) -> None:
        ex = fake(fill_ratio=Decimal("0.5"))
        r = market(ex, "BUY", "0.1", "a")
        assert r.status is OrderStatus.EXPIRED and r.executed_qty == Decimal("0.05")


class TestRejections:
    def test_reduce_only_cannot_increase(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        with pytest.raises(OrderRejectedError, match="ReduceOnly"):
            market(ex, "BUY", "0.1", "b", reduce=True)

    def test_reduce_only_cannot_flip(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        with pytest.raises(OrderRejectedError, match="ReduceOnly"):
            market(ex, "SELL", "0.2", "b", reduce=True)

    def test_reduce_only_without_position_is_rejected(self) -> None:
        with pytest.raises(OrderRejectedError, match="ReduceOnly"):
            market(fake(), "SELL", "0.1", "a", reduce=True)

    def test_insufficient_margin_is_rejected(self) -> None:
        ex = fake()  # 10,000 지갑 · 3배 → 명목 최대 30,000
        with pytest.raises(OrderRejectedError, match="Margin"):
            market(ex, "BUY", "0.7", "a")  # 35,000

    def test_lot_size_and_min_notional(self) -> None:
        ex = fake()
        with pytest.raises(OrderRejectedError, match="LOT_SIZE"):
            market(ex, "BUY", "0.0015", "a")
        with pytest.raises(OrderRejectedError, match="MIN_NOTIONAL"):
            market(ex, "BUY", "0.001", "b")  # 50 < 100

    def test_duplicate_client_order_id_is_rejected(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        with pytest.raises(OrderRejectedError, match="Duplicate"):
            market(ex, "BUY", "0.1", "a")
        assert ex.placed_count == 1


class TestProtectiveStop:
    def test_stop_rests_until_the_mark_crosses_it(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        stop = ex.place_stop_market("BTCUSDT", "SELL", Decimal(45_000), "s")
        assert stop.status is OrderStatus.NEW and stop.order_type is FuturesOrderType.STOP_MARKET
        assert ex.set_mark_price("BTCUSDT", Decimal(46_000)) == []
        assert ex.open_orders("BTCUSDT") == [stop]

    def test_gap_through_the_stop_fills_at_the_worse_price(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        ex.place_stop_market("BTCUSDT", "SELL", Decimal(45_000), "s")
        (done,) = ex.set_mark_price("BTCUSDT", Decimal(44_000))
        assert done.status is OrderStatus.FILLED
        assert done.avg_price == Decimal(44_000)  # 45,000 이 아니다
        assert ex.account().positions == {}
        assert ex.wallet == Decimal(10_000) - Decimal(600)

    def test_second_close_stop_expires_when_the_position_is_gone(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        ex.place_stop_market("BTCUSDT", "SELL", Decimal(45_000), "s1")
        ex.place_stop_market("BTCUSDT", "SELL", Decimal(40_000), "s2")
        ex.set_mark_price("BTCUSDT", Decimal(44_000))
        assert ex.orders["s2"].status is OrderStatus.EXPIRED

    def test_manual_close_expires_the_stop(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        ex.place_stop_market("BTCUSDT", "SELL", Decimal(45_000), "s")
        market(ex, "SELL", "0.1", "b", reduce=True)
        assert ex.orders["s"].status is OrderStatus.EXPIRED

    def test_a_stop_that_would_trigger_immediately_is_rejected(self) -> None:
        ex = fake()
        with pytest.raises(OrderRejectedError, match="immediately"):
            ex.place_stop_market("BTCUSDT", "SELL", Decimal(50_000), "s")

    def test_stop_price_must_fit_the_tick(self) -> None:
        ex = fake()
        with pytest.raises(OrderRejectedError, match="PRICE_FILTER"):
            ex.place_stop_market("BTCUSDT", "SELL", Decimal("45000.05"), "s")

    def test_cancel_and_cancel_again(self) -> None:
        ex = fake()
        ex.place_stop_market("BTCUSDT", "SELL", Decimal(45_000), "s")
        canceled = ex.cancel_order("BTCUSDT", "s")
        assert canceled is not None and canceled.status is OrderStatus.CANCELED
        with pytest.raises(OrderRejectedError):
            ex.cancel_order("BTCUSDT", "s")
        assert ex.cancel_order("BTCUSDT", "nope") is None


class TestFundingAndLiquidation:
    def test_long_pays_positive_funding(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.1", "a")
        change = ex.settle_funding("BTCUSDT", Decimal("0.0001"))
        assert change == Decimal("-0.5")  # 5,000 × 0.01%
        assert ex.wallet == Decimal("9999.5")

    def test_short_receives_positive_funding(self) -> None:
        ex = fake()
        market(ex, "SELL", "0.1", "a")
        assert ex.settle_funding("BTCUSDT", Decimal("0.0001")) == Decimal("0.5")

    def test_crash_liquidates_and_expires_stops(self) -> None:
        ex = fake()
        market(ex, "BUY", "0.6", "a")  # 30,000 명목, 3배
        ex.place_stop_market("BTCUSDT", "SELL", Decimal(1_000), "s")  # 너무 먼 스톱
        ex.set_mark_price("BTCUSDT", Decimal(33_500))
        assert ex.liquidations == 1
        assert ex.account().positions == {}
        assert ex.orders["s"].status is OrderStatus.EXPIRED
        assert ex.wallet >= 0


class TestFaults:
    def test_failure_before_accept_places_nothing(self) -> None:
        ex = fake(fail_before_accept=1)
        with pytest.raises(ExchangeError):
            market(ex, "BUY", "0.1", "a")
        assert ex.orders == {} and ex.account().positions == {}

    def test_lost_response_still_places_the_order(self) -> None:
        ex = fake(lose_response_after_accept=1)
        with pytest.raises(OrderStateUnknownError):
            market(ex, "BUY", "0.1", "a")
        assert ex.get_order("BTCUSDT", "a") is not None
        assert ex.account().positions["BTCUSDT"].quantity == Decimal("0.1")

    def test_failed_cancel_leaves_the_stop_working(self) -> None:
        ex = fake(fail_cancels=1)
        ex.place_stop_market("BTCUSDT", "SELL", Decimal(45_000), "s")
        with pytest.raises(ExchangeError):
            ex.cancel_order("BTCUSDT", "s")
        assert ex.orders["s"].status is OrderStatus.NEW
