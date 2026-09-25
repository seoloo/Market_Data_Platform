"""현물 데모 전용 주문 어댑터 — 네트워크 없이 MockTransport로 검증 (55)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import ConfigError, OrderRejectedError, OrderStateUnknownError
from auto_trading.exchange.binance_spot_demo import DEMO_URL, BinanceSpotDemo
from auto_trading.exchange.spot import OrderStatus

CREDS = BinanceCredentials("demo-key", "demo-secret")
Route = httpx.Response | Callable[[httpx.Request], httpx.Response]
ORDER = {
    "clientOrderId": "dt2-1",
    "orderId": 42,
    "symbol": "BTCUSDT",
    "side": "BUY",
    "status": "NEW",
    "origQty": "0.01",
    "executedQty": "0",
    "cummulativeQuoteQty": "0",
}


def adapter(routes: dict[tuple[str, str], Route]) -> tuple[BinanceSpotDemo, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_000_000})
        route = routes[(request.method, request.url.path)]
        return route(request) if callable(route) else route

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return BinanceSpotDemo(CREDS, client=client, clock=lambda: 1_000_000), seen


def params(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


@pytest.mark.parametrize(
    "url", ["https://api.binance.com", "https://testnet.binance.vision", "http://localhost"]
)
def test_non_demo_urls_are_refused(url: str) -> None:
    with pytest.raises(ConfigError, match="데모"):
        BinanceSpotDemo(CREDS, base_url=url)


def test_default_is_demo_mode() -> None:
    assert BinanceSpotDemo(CREDS).base_url == DEMO_URL


def test_limit_maker_is_post_only_and_signed() -> None:
    venue, seen = adapter({("POST", "/api/v3/order"): httpx.Response(200, json=ORDER)})
    result = venue.place_limit_maker(
        "BTCUSDT", "BUY", Decimal("0.0100"), Decimal("64000.10"), "dt2-1"
    )
    p = params(seen[-1])
    assert p["type"] == "LIMIT_MAKER"
    assert p["quantity"] == "0.01" and p["price"] == "64000.1"
    assert p["newClientOrderId"] == "dt2-1" and "signature" in p
    assert seen[-1].headers["X-MBX-APIKEY"] == "demo-key"
    assert seen[-1].url.host == "demo-api.binance.com"
    assert result.status is OrderStatus.NEW


def test_would_match_is_rejected_and_5xx_is_unknown() -> None:
    venue, _ = adapter(
        {("POST", "/api/v3/order"): httpx.Response(400, json={"code": -2010, "msg": "would match"})}
    )
    with pytest.raises(OrderRejectedError):
        venue.place_limit_maker("BTCUSDT", "BUY", Decimal("0.01"), Decimal("1"), "a")
    venue, _ = adapter({("POST", "/api/v3/order"): httpx.Response(503, json={})})
    with pytest.raises(OrderStateUnknownError):
        venue.place_market_order("BTCUSDT", "BUY", Decimal("0.01"), "b")


def test_fills_parse_maker_flag_and_commission() -> None:
    rows = [
        {
            "price": "64000",
            "qty": "0.01",
            "quoteQty": "640",
            "commission": "0.00001",
            "commissionAsset": "BTC",
            "isMaker": True,
        }
    ]
    venue, seen = adapter({("GET", "/api/v3/myTrades"): httpx.Response(200, json=rows)})
    (fill,) = venue.fills("BTCUSDT", "42")
    assert params(seen[-1])["orderId"] == "42"
    assert fill.is_maker and fill.commission_asset == "BTC" and fill.quote_qty == Decimal(640)
