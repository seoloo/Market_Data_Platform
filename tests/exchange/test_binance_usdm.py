"""Binance USD-M 서명 읽기 전용 어댑터 — 네트워크 없이 MockTransport로 검증.

특히 두 가지를 고정한다.

* 보호 스톱은 **Algo API** 로 조회한다 (2025-12-09 이후 일반 주문 경로에서 조건부 주문 거부).
* "없음"은 ``-2013`` 하나뿐이다. 다른 오류를 "없음"으로 읽으면 걸린 주문을 놓친다.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import ExchangeError, OrderRejectedError, RateLimitError
from auto_trading.exchange.binance_spot import sign
from auto_trading.exchange.binance_usdm import BinanceUsdmReadOnly, OrderPathDisabledError
from auto_trading.exchange.futures import FuturesOrderType, OrderStatus
from auto_trading.execution.futures_orders import FuturesOrderManager
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository

CREDS = BinanceCredentials("test-key", "test-secret")
Handler = Callable[[httpx.Request], httpx.Response]


def adapter(
    routes: dict[str, Handler | httpx.Response],
) -> tuple[BinanceUsdmReadOnly, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1_000_500})
        route = routes[request.url.path]
        return route(request) if callable(route) else route

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return BinanceUsdmReadOnly(CREDS, client=client, clock=lambda: 1_000_000), seen


def query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


def error(code: int, msg: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "msg": msg})


class TestSigning:
    def test_signed_requests_carry_key_timestamp_and_valid_signature(self) -> None:
        api, seen = adapter({"/fapi/v2/account": httpx.Response(200, json={"positions": []})})
        api.account()
        request = seen[-1]
        assert request.headers["X-MBX-APIKEY"] == "test-key"
        raw = urlparse(str(request.url)).query
        body, _, signature = raw.rpartition("&signature=")
        assert signature == sign("test-secret", body)
        assert query(request)["timestamp"] == str(1_000_000 + 500)  # 서버 시계 보정

    def test_clock_offset_is_measured_once(self) -> None:
        api, seen = adapter({"/fapi/v1/premiumIndex": httpx.Response(200, json={"markPrice": "1"})})
        api.clock_offset_ms()
        api.clock_offset_ms()
        assert sum(r.url.path == "/fapi/v1/time" for r in seen) == 1

    def test_transport_error_does_not_chain_the_url(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        api, _ = adapter({"/fapi/v1/premiumIndex": boom})
        with pytest.raises(ExchangeError) as info:
            api.mark_price("BTCUSDT")
        assert info.value.__cause__ is None and info.value.__suppress_context__

    def test_non_json_response_is_an_exchange_error(self) -> None:
        """없는 경로는 404 HTML 을 준다. 파싱 오류로 죽지 않고 계약대로 ExchangeError 여야 한다."""
        api, _ = adapter({"/fapi/v1/premiumIndex": httpx.Response(404, text="<html>Not Found")})
        with pytest.raises(ExchangeError, match="JSON"):
            api.mark_price("BTCUSDT")

    def test_rate_limit(self) -> None:
        api, _ = adapter({"/fapi/v1/premiumIndex": error(-1003, "too many", 429)})
        with pytest.raises(RateLimitError):
            api.mark_price("BTCUSDT")


EXCHANGE_INFO: dict[str, object] = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.002"},
                {"filterType": "MIN_NOTIONAL", "notional": "100"},
            ],
        }
    ]
}


class TestMarketAndAccount:
    def test_filters_prefer_market_lot_size_and_are_cached(self) -> None:
        api, seen = adapter({"/fapi/v1/exchangeInfo": httpx.Response(200, json=EXCHANGE_INFO)})
        f = api.filters("BTCUSDT")
        api.filters("BTCUSDT")
        assert (f.step_size, f.min_qty, f.min_notional, f.tick_size) == (
            Decimal("0.001"),
            Decimal("0.002"),
            Decimal(100),
            Decimal("0.1"),
        )
        assert sum(r.url.path == "/fapi/v1/exchangeInfo" for r in seen) == 1

    def test_unknown_symbol_raises(self) -> None:
        api, _ = adapter({"/fapi/v1/exchangeInfo": httpx.Response(200, json=EXCHANGE_INFO)})
        with pytest.raises(ExchangeError, match="없는 심볼"):
            api.filters("NOPEUSDT")

    def test_account_keeps_only_open_positions(self) -> None:
        body = {
            "totalWalletBalance": "1000.5",
            "availableBalance": "800.25",
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.010",
                    "entryPrice": "50000.0",
                    "leverage": "3",
                },
                {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0", "leverage": "20"},
                {"symbol": "SOLUSDT", "positionAmt": "-2", "entryPrice": "150", "leverage": "2"},
            ],
        }
        api, _ = adapter({"/fapi/v2/account": httpx.Response(200, json=body)})
        acct = api.account()
        assert acct.wallet_balance == Decimal("1000.5")
        assert acct.available_balance == Decimal("800.25")
        assert set(acct.positions) == {"BTCUSDT", "SOLUSDT"}
        assert acct.positions["SOLUSDT"].quantity == Decimal(-2)
        assert acct.positions["BTCUSDT"].leverage == 3

    def test_mark_price(self) -> None:
        api, seen = adapter(
            {"/fapi/v1/premiumIndex": httpx.Response(200, json={"markPrice": "50123.4"})}
        )
        assert api.mark_price("BTCUSDT") == Decimal("50123.4")
        assert query(seen[-1])["symbol"] == "BTCUSDT"


ORDER = {
    "clientOrderId": "o1-1-BTCUSDT-OB",
    "orderId": 42,
    "symbol": "BTCUSDT",
    "side": "BUY",
    "status": "FILLED",
    "type": "MARKET",
    "origQty": "0.010",
    "executedQty": "0.010",
    "avgPrice": "50010.0",
    "reduceOnly": False,
    "closePosition": False,
    "stopPrice": "0",
}


class TestOrders:
    def test_get_order_maps_fields(self) -> None:
        api, seen = adapter({"/fapi/v1/order": httpx.Response(200, json=ORDER)})
        r = api.get_order("BTCUSDT", "o1-1-BTCUSDT-OB")
        assert r is not None
        assert (r.status, r.executed_qty, r.avg_price) == (
            OrderStatus.FILLED,
            Decimal("0.010"),
            Decimal("50010.0"),
        )
        assert query(seen[-1])["origClientOrderId"] == "o1-1-BTCUSDT-OB"

    def test_order_does_not_exist_is_none(self) -> None:
        api, _ = adapter({"/fapi/v1/order": error(-2013, "Order does not exist")})
        assert api.get_order("BTCUSDT", "x") is None

    def test_other_errors_are_not_read_as_missing(self) -> None:
        """-2015(권한) 같은 오류를 '없음'으로 읽으면 걸린 주문을 NOT_PLACED 로 닫게 된다."""
        api, _ = adapter({"/fapi/v1/order": error(-2015, "Invalid API-key", 401)})
        with pytest.raises(ExchangeError, match="-2015"):
            api.get_order("BTCUSDT", "x")


ALGO_NEW = {
    "algoId": 7,
    "clientAlgoId": "o1-1-BTCUSDT-PS",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "orderType": "STOP_MARKET",
    "algoStatus": "NEW",
    "triggerPrice": "45000",
    "closePosition": True,
    "reduceOnly": True,
    "quantity": "0",
    "actualOrderId": "",
}


class TestStops:
    def test_stops_use_the_algo_endpoints(self) -> None:
        api, seen = adapter(
            {
                "/fapi/v1/algoOrder": httpx.Response(200, json=ALGO_NEW),
                "/fapi/v1/openAlgoOrders": httpx.Response(200, json=[ALGO_NEW]),
            }
        )
        stop = api.get_stop("BTCUSDT", "o1-1-BTCUSDT-PS")
        (listed,) = api.open_stops("BTCUSDT")
        assert stop is not None and stop.order_type is FuturesOrderType.STOP_MARKET
        assert stop.status is OrderStatus.NEW and stop.stop_price == Decimal(45_000)
        assert listed == stop
        assert query(seen[-2])["clientAlgoId"] == "o1-1-BTCUSDT-PS"
        assert "/fapi/v1/order" not in {r.url.path for r in seen}

    def test_triggered_stop_reads_the_actual_fill(self) -> None:
        triggered = dict(ALGO_NEW, algoStatus="TRIGGERED", actualOrderId="99")
        fill = dict(ORDER, side="SELL", orderId=99, executedQty="0.010", avgPrice="44000")
        api, seen = adapter(
            {
                "/fapi/v1/algoOrder": httpx.Response(200, json=triggered),
                "/fapi/v1/order": httpx.Response(200, json=fill),
            }
        )
        stop = api.get_stop("BTCUSDT", "o1-1-BTCUSDT-PS")
        assert stop is not None
        assert (stop.status, stop.executed_qty, stop.avg_price) == (
            OrderStatus.FILLED,
            Decimal("0.010"),
            Decimal(44_000),
        )
        assert query(seen[-1])["orderId"] == "99"

    def test_triggered_without_actual_order_stays_working(self) -> None:
        pending = dict(ALGO_NEW, algoStatus="TRIGGERED", actualOrderId="")
        api, _ = adapter({"/fapi/v1/algoOrder": httpx.Response(200, json=pending)})
        stop = api.get_stop("BTCUSDT", "o1-1-BTCUSDT-PS")
        assert stop is not None and stop.status is OrderStatus.NEW

    @pytest.mark.parametrize("status", ["CANCELED", "EXPIRED"])
    def test_finished_without_fill(self, status: str) -> None:
        api, _ = adapter(
            {"/fapi/v1/algoOrder": httpx.Response(200, json=dict(ALGO_NEW, algoStatus=status))}
        )
        stop = api.get_stop("BTCUSDT", "o1-1-BTCUSDT-PS")
        assert stop is not None and stop.status is OrderStatus(status)

    def test_stop_on_another_symbol_is_not_ours(self) -> None:
        api, _ = adapter(
            {"/fapi/v1/algoOrder": httpx.Response(200, json=dict(ALGO_NEW, symbol="ETHUSDT"))}
        )
        assert api.get_stop("BTCUSDT", "o1-1-BTCUSDT-PS") is None

    def test_undocumented_algo_errors_raise(self) -> None:
        api, _ = adapter({"/fapi/v1/algoOrder": error(-1000, "unknown")})
        with pytest.raises(ExchangeError):
            api.get_stop("BTCUSDT", "o1-1-BTCUSDT-PS")


class TestOrderPathIsClosed:
    def test_every_order_method_refuses_without_a_request(self) -> None:
        api, seen = adapter({})
        calls: list[Callable[[], object]] = [
            lambda: api.place_market_order(
                "BTCUSDT", "BUY", Decimal("0.01"), "x", reduce_only=False
            ),
            lambda: api.place_stop_market("BTCUSDT", "SELL", Decimal(1), "x"),
            lambda: api.cancel_order("BTCUSDT", "x"),
            lambda: api.cancel_stop("BTCUSDT", "x"),
        ]
        for call in calls:
            with pytest.raises(OrderPathDisabledError):
                call()
        assert seen == []

    def test_refusal_is_a_certain_rejection_not_unknown(self) -> None:
        """관리자가 UNKNOWN 으로 기록해 신규 진입을 헛되이 막지 않는다."""
        assert issubclass(OrderPathDisabledError, OrderRejectedError)
        api, _ = adapter({})
        conn = connect(":memory:")
        apply_migrations(conn, now_ms=1)
        ticks = itertools.count(1)
        m = FuturesOrderManager(
            api,
            FuturesOrderRepository(conn),
            mode="live",
            strategy_id="o1_overlay",
            strategy_tag="o1",
            clock=lambda: next(ticks),
        )
        record = m.submit_market(
            decision_ms=1, symbol="BTCUSDT", side="BUY", quantity=Decimal("0.01"), purpose="OPEN"
        )
        assert record.state == "REJECTED" and not m.blocked()
