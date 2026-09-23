"""Binance 현물 읽기 전용 어댑터 — 네트워크 없이 MockTransport로 검증. `.env` 로더 포함."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from auto_trading.config.secrets import (
    BinanceCredentials,
    load_binance_credentials,
    read_env_file,
)
from auto_trading.core.errors import ConfigError, ExchangeError, RateLimitError
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly, sign
from auto_trading.exchange.spot import OrderStatus

CREDS = BinanceCredentials("test-key", "test-secret")


def test_signature_matches_binance_documentation_vector() -> None:
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    query = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
        "&recvWindow=5000&timestamp=1499827319559"
    )
    assert sign(secret, query) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def adapter(handler: object) -> BinanceSpotReadOnly:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return BinanceSpotReadOnly(CREDS, client=client, clock=lambda: 1_000_000)


def routes(extra: dict[str, httpx.Response]) -> object:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_000_500})
        return extra[request.url.path]

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def test_signed_request_has_key_header_timestamp_offset_and_valid_signature() -> None:
    handler = routes({"/api/v3/account": httpx.Response(200, json={"balances": []})})
    adapter(handler).balances()
    request = handler.seen[-1]  # type: ignore[attr-defined]
    assert request.headers["X-MBX-APIKEY"] == "test-key"
    query = urlparse(str(request.url)).query
    body, _, signature = query.rpartition("&signature=")
    assert sign("test-secret", body) == signature
    assert parse_qs(body)["timestamp"] == ["1000500"]  # 로컬 1,000,000 + 서버 차이 500


def test_balances_filters_and_price_are_parsed() -> None:
    handler = routes(
        {
            "/api/v3/account": httpx.Response(
                200,
                json={
                    "balances": [
                        {"asset": "USDT", "free": "100.5", "locked": "0.5"},
                        {"asset": "BTC", "free": "0.00000000", "locked": "0.00000000"},
                    ]
                },
            ),
            "/api/v3/exchangeInfo": httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "filters": [
                                {
                                    "filterType": "LOT_SIZE",
                                    "stepSize": "0.00001000",
                                    "minQty": "0.00001000",
                                },
                                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                            ],
                        }
                    ]
                },
            ),
            "/api/v3/ticker/price": httpx.Response(200, json={"price": "65000.01"}),
        }
    )
    ex = adapter(handler)
    assert ex.balances() == {"USDT": Decimal("101.0")}
    f = ex.filters("BTCUSDT")
    assert (f.step_size, f.min_qty, f.min_notional) == (
        Decimal("0.00001"),
        Decimal("0.00001"),
        Decimal(5),
    )
    assert ex.last_price("BTCUSDT") == Decimal("65000.01")


def test_get_order_maps_status_and_missing_order_is_none() -> None:
    order = {
        "clientOrderId": "t2l-1-BTCUSDT-B",
        "orderId": 42,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "status": "FILLED",
        "origQty": "0.01",
        "executedQty": "0.01",
        "cummulativeQuoteQty": "650.0",
    }
    ex = adapter(routes({"/api/v3/order": httpx.Response(200, json=order)}))
    result = ex.get_order("BTCUSDT", "t2l-1-BTCUSDT-B")
    assert result is not None and result.status is OrderStatus.FILLED
    assert result.exchange_order_id == "42" and result.quote_qty == Decimal("650.0")
    missing = httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})
    assert adapter(routes({"/api/v3/order": missing})).get_order("BTCUSDT", "x") is None


def test_errors_are_mapped_and_ordering_is_refused() -> None:
    limited = adapter(routes({"/api/v3/ticker/price": httpx.Response(429, json={})}))
    with pytest.raises(RateLimitError):
        limited.last_price("BTCUSDT")
    denied = httpx.Response(401, json={"code": -2015, "msg": "Invalid API-key, IP, or permissions"})
    with pytest.raises(ExchangeError, match="-2015"):
        adapter(routes({"/api/v3/account": denied})).balances()
    with pytest.raises(ExchangeError, match="읽기 전용"):
        adapter(routes({})).place_market_order("BTCUSDT", "BUY", Decimal(1), "c")


def test_api_restrictions_keeps_boolean_flags() -> None:
    body = {"ipRestrict": True, "enableReading": True, "enableWithdrawals": False, "createTime": 1}
    ex = adapter(routes({"/sapi/v1/account/apiRestrictions": httpx.Response(200, json=body)}))
    assert ex.api_restrictions() == {
        "ipRestrict": True,
        "enableReading": True,
        "enableWithdrawals": False,
    }


class TestSecrets:
    def test_env_file_parsing_and_masked_repr(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "# 주석\nAT_MODE=paper   # 인라인 주석\nBINANCE_API_KEY=abcd1234\n"
            'BINANCE_API_SECRET="s3cr3t"\nEMPTY=\n',
            encoding="utf-8",
        )
        assert read_env_file(env)["AT_MODE"] == "paper"
        creds = load_binance_credentials(env, environ={})
        assert creds.api_secret == "s3cr3t"
        assert "s3cr3t" not in repr(creds) and "abcd1234" not in str(creds)

    def test_missing_credentials_raise_without_leaking(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="BINANCE_API_KEY"):
            load_binance_credentials(tmp_path / "none.env", environ={})

    def test_environment_overrides_file(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("BINANCE_API_KEY=file\nBINANCE_API_SECRET=file\n", encoding="utf-8")
        creds = load_binance_credentials(env, environ={"BINANCE_API_KEY": "envkey"})
        assert creds.api_key == "envkey" and creds.api_secret == "file"
