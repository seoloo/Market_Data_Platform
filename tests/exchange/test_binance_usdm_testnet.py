"""테스트넷 전용 주문 어댑터 — 네트워크 없이 MockTransport로 검증.

1. 테스트넷이 아닌 주소로는 **만들어지지 않는다** (실계좌 주문 경로 없음).
2. 전송 결과 분류: 거래소가 코드로 거절 → 확실한 거절, 그 밖(네트워크·5xx·-1007·레이트 리밋) → 모름.
3. 보호 스톱은 Algo API 로 간다.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import ConfigError, OrderRejectedError, OrderStateUnknownError
from auto_trading.exchange.binance_usdm_testnet import TESTNET_URL, BinanceUsdmTestnet
from auto_trading.exchange.futures import OrderStatus
from auto_trading.execution.futures_orders import FuturesOrderManager
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository

CREDS = BinanceCredentials("tn-key", "tn-secret")
Route = Callable[[httpx.Request], httpx.Response] | httpx.Response

FILLED = {
    "clientOrderId": "tn-1-BTCUSDT-OB",
    "orderId": 11,
    "symbol": "BTCUSDT",
    "side": "BUY",
    "status": "FILLED",
    "type": "MARKET",
    "origQty": "0.002",
    "executedQty": "0.002",
    "avgPrice": "84000.0",
    "reduceOnly": False,
    "closePosition": False,
    "stopPrice": "0",
}
ALGO = {
    "algoId": 5,
    "clientAlgoId": "tn-1-BTCUSDT-PS",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "algoStatus": "NEW",
    "triggerPrice": "75000",
    "closePosition": True,
    "reduceOnly": True,
    "quantity": "0",
    "actualOrderId": "",
}


def adapter(
    routes: dict[tuple[str, str], Route],
) -> tuple[BinanceUsdmTestnet, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1_000_000})
        route = routes[(request.method, request.url.path)]
        return route(request) if callable(route) else route

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return BinanceUsdmTestnet(CREDS, client=client, clock=lambda: 1_000_000), seen


def params(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


def error(code: int, msg: str = "x", status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "msg": msg})


class TestTestnetOnly:
    @pytest.mark.parametrize(
        "url", ["https://fapi.binance.com", "https://api.binance.com", "http://localhost"]
    )
    def test_non_testnet_urls_are_refused_at_construction(self, url: str) -> None:
        with pytest.raises(ConfigError, match="테스트넷"):
            BinanceUsdmTestnet(CREDS, base_url=url)

    def test_default_is_the_documented_testnet(self) -> None:
        api, seen = adapter(
            {("GET", "/fapi/v1/premiumIndex"): httpx.Response(200, json={"markPrice": "1"})}
        )
        api.mark_price("BTCUSDT")
        assert TESTNET_URL == "https://demo-fapi.binance.com"
        assert seen[-1].url.host == "demo-fapi.binance.com"


class TestOrders:
    def test_market_order_request(self) -> None:
        api, seen = adapter({("POST", "/fapi/v1/order"): httpx.Response(200, json=FILLED)})
        r = api.place_market_order(
            "BTCUSDT", "BUY", Decimal("2E-3"), "tn-1-BTCUSDT-OB", reduce_only=False
        )
        p = params(seen[-1])
        assert seen[-1].method == "POST"
        assert p["type"] == "MARKET" and p["quantity"] == "0.002"  # 지수 표기 아님
        assert p["reduceOnly"] == "false" and p["newOrderRespType"] == "RESULT"
        assert p["newClientOrderId"] == "tn-1-BTCUSDT-OB"
        assert r.status is OrderStatus.FILLED and r.avg_price == Decimal(84_000)

    def test_stop_goes_to_the_algo_api(self) -> None:
        api, seen = adapter({("POST", "/fapi/v1/algoOrder"): httpx.Response(200, json=ALGO)})
        r = api.place_stop_market("BTCUSDT", "SELL", Decimal("75000.0"), "tn-1-BTCUSDT-PS")
        p = params(seen[-1])
        assert (p["algoType"], p["type"], p["closePosition"], p["workingType"]) == (
            "CONDITIONAL",
            "STOP_MARKET",
            "true",
            "MARK_PRICE",
        )
        assert p["clientAlgoId"] == "tn-1-BTCUSDT-PS" and p["triggerPrice"] == "75000"
        assert "quantity" not in p  # closePosition 과 함께 쓸 수 없다
        assert r.status is OrderStatus.NEW

    def test_limit_entry_is_post_only_gtx(self) -> None:
        """Elliott 데모(56) 되돌림 예약 진입."""
        new = {**FILLED, "status": "NEW", "executedQty": "0", "avgPrice": "0", "type": "LIMIT"}
        api, seen = adapter({("POST", "/fapi/v1/order"): httpx.Response(200, json=new)})
        r = api.place_limit_order("BTCUSDC", "BUY", Decimal("0.0120"), Decimal("61234.50"), "ew-1")
        p = params(seen[-1])
        assert (p["type"], p["timeInForce"]) == ("LIMIT", "GTX")
        assert p["quantity"] == "0.012" and p["price"] == "61234.5"
        assert "reduceOnly" not in p
        assert r.status is OrderStatus.NEW

    def test_take_profit_is_close_position_on_mark_price(self) -> None:
        api, seen = adapter({("POST", "/fapi/v1/algoOrder"): httpx.Response(200, json=ALGO)})
        api.place_take_profit_market("BTCUSDC", "SELL", Decimal("70500.0"), "ew-1-T")
        p = params(seen[-1])
        assert (p["type"], p["closePosition"], p["workingType"]) == (
            "TAKE_PROFIT_MARKET",
            "true",
            "MARK_PRICE",
        )
        assert p["triggerPrice"] == "70500" and "quantity" not in p

    def test_trade_fills_keep_non_usdt_commission(self) -> None:
        rows = [
            {
                "price": "61234.5",
                "qty": "0.012",
                "quoteQty": "734.814",
                "commission": "0.1469",
                "commissionAsset": "USDC",
                "maker": True,
                "realizedPnl": "0",
            }
        ]
        api, _ = adapter({("GET", "/fapi/v1/userTrades"): httpx.Response(200, json=rows)})
        (fill,) = api.trade_fills("BTCUSDC", "11")
        assert fill["commission_asset"] == "USDC" and fill["maker"] is True
        assert fill["quote"] == Decimal("734.814")

    def test_quantity_stop_is_reduce_only_without_close_position(self) -> None:
        """공백 없는 스톱 교체 훈련(52)용 수량 지정 스톱."""
        api, seen = adapter({("POST", "/fapi/v1/algoOrder"): httpx.Response(200, json=ALGO)})
        api.place_stop_market(
            "BTCUSDT", "SELL", Decimal("75000.0"), "tsr-1-C", quantity=Decimal("0.010")
        )
        p = params(seen[-1])
        assert p["quantity"] == "0.01" and p["reduceOnly"] == "true"
        assert "closePosition" not in p and p["workingType"] == "MARK_PRICE"


class TestSendClassification:
    @pytest.mark.parametrize(
        "response",
        [error(-2022, "ReduceOnly Order is rejected"), error(-4164, "notional"), error(-2021)],
    )
    def test_exchange_rejection_is_certain(self, response: httpx.Response) -> None:
        api, _ = adapter({("POST", "/fapi/v1/order"): response})
        with pytest.raises(OrderRejectedError):
            api.place_market_order("BTCUSDT", "BUY", Decimal("0.002"), "x", reduce_only=False)

    @pytest.mark.parametrize(
        "response",
        [
            error(-1007, "Timeout waiting for response from backend server"),
            error(-1000, "Unknown error", 503),
            error(-1003, "too many", 429),
            httpx.Response(502, text="<html>bad gateway"),
        ],
    )
    def test_uncertain_outcomes_are_unknown(self, response: httpx.Response) -> None:
        api, _ = adapter({("POST", "/fapi/v1/order"): response})
        with pytest.raises(OrderStateUnknownError):
            api.place_market_order("BTCUSDT", "BUY", Decimal("0.002"), "x", reduce_only=False)

    def test_network_failure_is_unknown(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        api, _ = adapter({("POST", "/fapi/v1/algoOrder"): boom})
        with pytest.raises(OrderStateUnknownError):
            api.place_stop_market("BTCUSDT", "SELL", Decimal(75_000), "x")


class TestCancel:
    def test_cancel_stop_then_reads_final_state(self) -> None:
        api, seen = adapter(
            {
                ("DELETE", "/fapi/v1/algoOrder"): httpx.Response(
                    200, json={"algoId": 5, "clientAlgoId": "s", "code": "200", "msg": "success"}
                ),
                ("GET", "/fapi/v1/algoOrder"): httpx.Response(
                    200, json=dict(ALGO, algoStatus="CANCELED")
                ),
            }
        )
        r = api.cancel_stop("BTCUSDT", "tn-1-BTCUSDT-PS")
        assert r is not None and r.status is OrderStatus.CANCELED
        assert [q.method for q in seen if q.url.path == "/fapi/v1/algoOrder"] == ["DELETE", "GET"]

    def test_missing_order_is_none(self) -> None:
        api, _ = adapter({("DELETE", "/fapi/v1/algoOrder"): error(-2013, "Order does not exist")})
        assert api.cancel_stop("BTCUSDT", "x") is None

    def test_already_finished_is_a_rejection(self) -> None:
        api, _ = adapter({("DELETE", "/fapi/v1/order"): error(-2011, "Unknown order sent")})
        with pytest.raises(OrderRejectedError):
            api.cancel_order("BTCUSDT", "x")


class TestAccountSetup:
    def test_margin_type_already_set_is_fine(self) -> None:
        api, _ = adapter({("POST", "/fapi/v1/marginType"): error(-4046, "No need to change")})
        api.set_margin_type("BTCUSDT", "CROSSED")

    def test_other_margin_type_errors_raise(self) -> None:
        api, _ = adapter({("POST", "/fapi/v1/marginType"): error(-4067, "Open orders exist")})
        with pytest.raises(Exception, match="-4067"):
            api.set_margin_type("BTCUSDT", "CROSSED")

    def test_leverage_and_position_mode(self) -> None:
        api, seen = adapter(
            {
                ("POST", "/fapi/v1/leverage"): httpx.Response(200, json={"leverage": 3}),
                ("GET", "/fapi/v1/positionSide/dual"): httpx.Response(
                    200, json={"dualSidePosition": False}
                ),
            }
        )
        assert api.set_leverage("BTCUSDT", 3) == 3
        assert api.dual_side_position() is False
        assert params(seen[-2])["leverage"] == "3"


class TestWithOrderManager:
    def _manager(self, api: BinanceUsdmTestnet) -> FuturesOrderManager:
        conn = connect(":memory:")
        apply_migrations(conn, now_ms=1)
        ticks = itertools.count(1)
        return FuturesOrderManager(
            api,
            FuturesOrderRepository(conn),
            mode="testnet",
            strategy_id="drill",
            strategy_tag="tn",
            clock=lambda: next(ticks),
        )

    def test_exchange_rejection_is_recorded_as_rejected(self) -> None:
        api, _ = adapter({("POST", "/fapi/v1/order"): error(-2022, "ReduceOnly Order is rejected")})
        m = self._manager(api)
        r = m.submit_market(
            decision_ms=1, symbol="BTCUSDT", side="SELL", quantity=Decimal("0.002"), purpose="CLOSE"
        )
        assert r.state == "REJECTED" and "-2022" in (r.error or "") and not m.blocked()

    def test_unknown_send_is_resolved_by_lookup(self) -> None:
        api, _ = adapter(
            {
                ("POST", "/fapi/v1/order"): error(-1000, "Unknown error", 503),
                ("GET", "/fapi/v1/order"): httpx.Response(200, json=FILLED),
            }
        )
        m = self._manager(api)
        r = m.submit_market(
            decision_ms=1, symbol="BTCUSDT", side="BUY", quantity=Decimal("0.002"), purpose="OPEN"
        )
        assert r.state == "UNKNOWN" and m.blocked()
        (resolved,) = m.recover()
        assert resolved.state == "FILLED" and not m.blocked()


class TestTestnetFindings:
    """1차 테스트넷 훈련(2026-09-24)에서 실측한 동작."""

    def test_zero_avg_price_is_computed_from_cum_quote(self) -> None:
        body = dict(FILLED, avgPrice="0.00", cumQuote="168.00")
        api, _ = adapter({("POST", "/fapi/v1/order"): httpx.Response(200, json=body)})
        r = api.place_market_order("BTCUSDT", "BUY", Decimal("0.002"), "x", reduce_only=False)
        assert r.avg_price == Decimal(84_000)

    def test_zero_avg_price_without_cum_quote_is_looked_up(self) -> None:
        body = dict(FILLED, avgPrice="0.00")
        api, seen = adapter(
            {
                ("POST", "/fapi/v1/order"): httpx.Response(200, json=body),
                ("GET", "/fapi/v1/order"): httpx.Response(200, json=FILLED),
            }
        )
        r = api.place_market_order("BTCUSDT", "BUY", Decimal("0.002"), "x", reduce_only=False)
        assert r.avg_price == Decimal(84_000)
        assert [q.method for q in seen if q.url.path == "/fapi/v1/order"] == ["POST", "GET"]

    def test_stale_new_after_successful_stop_cancel_is_canceled(self) -> None:
        api, _ = adapter(
            {
                ("DELETE", "/fapi/v1/algoOrder"): httpx.Response(
                    200, json={"algoId": 5, "clientAlgoId": "s", "code": "200", "msg": "success"}
                ),
                ("GET", "/fapi/v1/algoOrder"): httpx.Response(200, json=ALGO),  # 아직 NEW
            }
        )
        r = api.cancel_stop("BTCUSDT", "tn-1-BTCUSDT-PS")
        assert r is not None and r.status is OrderStatus.CANCELED

    def test_order_cancel_uses_the_delete_response(self) -> None:
        canceled = dict(FILLED, status="CANCELED", executedQty="0")
        api, seen = adapter({("DELETE", "/fapi/v1/order"): httpx.Response(200, json=canceled)})
        r = api.cancel_order("BTCUSDT", "x")
        assert r is not None and r.status is OrderStatus.CANCELED
        assert [q.method for q in seen if q.url.path == "/fapi/v1/order"] == ["DELETE"]
