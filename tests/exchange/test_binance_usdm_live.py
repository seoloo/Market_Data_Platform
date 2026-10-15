"""Altair 실계좌 선물 어댑터 — 네트워크 없이 MockTransport 로.

1. 실계좌 주소만 받는다. 2. 주문 허용이 꺼져 있으면 조회만 되고 주문 · 취소 · 설정은 막힌다.
3. 시장가 주문 · 목표가 시장가 청산은 언제나 막힌다(사용자 규칙).
   비상 STOP_MARKET · 지정가 IOC 는 간다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import ConfigError
from auto_trading.exchange.binance_usdm_live import (
    LIVE_URL,
    BinanceUsdmLive,
    load_live_futures,
)
from auto_trading.exchange.overseas import TradingDisabledError

CREDS = BinanceCredentials("k", "s")
FILLED = {
    "clientOrderId": "altair-lg-BTC-1",
    "orderId": 11,
    "symbol": "BTCUSDC",
    "side": "BUY",
    "status": "FILLED",
    "type": "LIMIT",
    "origQty": "0.002",
    "executedQty": "0.002",
    "avgPrice": "84000.0",
    "reduceOnly": False,
    "closePosition": False,
    "stopPrice": "0",
}
ALGO = {
    "algoId": 5,
    "clientAlgoId": "altair-st-BTC-1",
    "symbol": "BTCUSDC",
    "side": "SELL",
    "algoStatus": "NEW",
    "triggerPrice": "67000",
    "closePosition": True,
    "reduceOnly": True,
    "quantity": "0",
    "actualOrderId": "",
}


def adapter(*, enabled: bool = True) -> tuple[BinanceUsdmLive, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1_000_000})
        if path == "/fapi/v1/order":
            return httpx.Response(200, json=FILLED)
        if path == "/fapi/v1/userTrades":
            return httpx.Response(200, json=[])
        if path == "/fapi/v1/algoOrder":
            return httpx.Response(200, json=ALGO)
        if path == "/fapi/v1/income":
            return httpx.Response(
                200,
                json=[
                    {"asset": "USDC", "income": "500", "time": 20, "tranId": 2},
                    {"asset": "USDT", "income": "9", "time": 10, "tranId": 1},
                    {"asset": "USDC", "income": "-100", "time": 15, "tranId": 3},
                ],
            )
        if path == "/fapi/v1/multiAssetsMargin":
            return httpx.Response(200, json={"multiAssetsMargin": False})
        if path == "/fapi/v1/leverage":
            return httpx.Response(200, json={"leverage": 5})
        raise AssertionError(path)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return BinanceUsdmLive(
        CREDS, trading_enabled=enabled, client=client, clock=lambda: 1_000_000
    ), seen


def _params(r: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(r.url)).query).items()}


def test_only_mainnet_url() -> None:
    assert LIVE_URL == "https://fapi.binance.com"
    for url in ("https://demo-fapi.binance.com", "https://api.binance.com", "http://localhost"):
        with pytest.raises(ConfigError, match="실계좌"):
            BinanceUsdmLive(CREDS, trading_enabled=True, base_url=url)


def test_switch_off_reads_but_never_sends() -> None:
    a, seen = adapter(enabled=False)
    assert a.multi_assets_margin() is False
    assert a.transfers("USDC", 0) == [(15, Decimal("-100"), "3"), (20, Decimal("500"), "2")]
    with pytest.raises(TradingDisabledError):
        a.place_limit_ioc(
            "BTCUSDC", "BUY", Decimal("0.002"), Decimal("84000"), "c", reduce_only=False
        )
    with pytest.raises(TradingDisabledError):
        a.place_stop_market("BTCUSDC", "SELL", Decimal("67000"), "s")
    with pytest.raises(TradingDisabledError):
        a.cancel_stop("BTCUSDC", "s")
    with pytest.raises(TradingDisabledError):
        a.set_leverage("BTCUSDC", 5)
    with pytest.raises(TradingDisabledError):
        a.set_margin_type("BTCUSDC", "CROSSED")
    assert all(r.method == "GET" for r in seen)


def test_market_orders_always_refused_limit_and_stop_go() -> None:
    a, seen = adapter()
    with pytest.raises(TradingDisabledError, match="시장가"):
        a.place_market_order("BTCUSDC", "BUY", Decimal("0.002"), "m", reduce_only=False)
    with pytest.raises(TradingDisabledError, match="시장가"):
        a.place_take_profit_market("BTCUSDC", "SELL", Decimal("90000"), "tp")
    assert not [r for r in seen if r.method == "POST"]
    r = a.place_limit_ioc(
        "BTCUSDC", "BUY", Decimal("0.002"), Decimal("84252"), "altair-lg-BTC-1", reduce_only=False
    )
    assert r.executed_qty == Decimal("0.002")
    p = _params(next(x for x in seen if x.url.path == "/fapi/v1/order"))
    assert (p["type"], p["timeInForce"], p["price"]) == ("LIMIT", "IOC", "84252")
    a.place_stop_market("BTCUSDC", "SELL", Decimal("67000"), "altair-st-BTC-1")
    p = _params(next(x for x in seen if x.url.path == "/fapi/v1/algoOrder"))
    assert (p["type"], p["closePosition"]) == ("STOP_MARKET", "true")
    assert str(seen[-1].url).startswith(LIVE_URL)


def test_keys_prefer_futures_then_fall_back(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_live_futures(env, environ={})
    creds, on = load_live_futures(
        env, environ={"BINANCE_LIVE_API_KEY": "spot", "BINANCE_LIVE_API_SECRET": "x"}
    )
    assert creds.api_key == "spot" and on is False
    creds, on = load_live_futures(
        env,
        environ={
            "BINANCE_LIVE_API_KEY": "spot",
            "BINANCE_LIVE_API_SECRET": "x",
            "BINANCE_FUTURES_LIVE_API_KEY": "fut",
            "BINANCE_FUTURES_LIVE_API_SECRET": "y",
            "BINANCE_FUTURES_LIVE_TRADING_ENABLED": "TRUE",
        },
    )
    assert creds.api_key == "fut" and on is True
