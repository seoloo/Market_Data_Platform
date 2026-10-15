"""토스증권 어댑터 (문서 118 §6) — 가짜 서버로만. 실제 주문 없음."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.config.secrets import TossCredentials, load_toss_credentials
from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.exchange.overseas import TradingDisabledError
from auto_trading.exchange.toss import TossApiError, TossReader, TossTokenCache, TossTrader

NOW = 1_790_000_000_000


def _creds(enabled: bool = True) -> TossCredentials:
    return TossCredentials("client-abcdef123456", "secret-xyz", 1, enabled)


def _handler(routes: dict[str, Any], seen: list[httpx.Request]) -> Callable[..., httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 86399})
        value = routes.get(f"{request.method} {request.url.path}")
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json={"result": value})

    return handle


def _make(
    tmp_path: Path, routes: dict[str, Any], seen: list[httpx.Request], *, enabled: bool = True
) -> TossTrader:
    return TossTrader(
        _creds(enabled),
        TossTokenCache(tmp_path / "tok.json"),
        client=httpx.Client(transport=httpx.MockTransport(_handler(routes, seen))),
        clock=lambda: NOW,
        sleep=lambda _s: None,
        allowed_symbols=frozenset({"QNDX", "TQQQ"}),
        max_qty={"TQQQ": 60},
        sell_symbols=frozenset({"TQQQ"}),
        max_limit_usd=Decimal("250"),
    )


def test_credentials_masked_and_switch(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("TOSS_CLIENT_ID=abc\nTOSS_CLIENT_SECRET=def\n", encoding="utf-8")
    c = load_toss_credentials(env, environ={})
    assert not c.trading_enabled and c.account_seq == 1
    assert "abc" not in repr(c) and "def" not in repr(c)
    c2 = load_toss_credentials(env, environ={"TOSS_TRADING_ENABLED": "true"})
    assert c2.trading_enabled


def test_reads_and_token_is_cached(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    routes = {
        "GET /api/v1/prices": [{"symbol": "QQQ", "lastPrice": "749.56"}],
        "GET /api/v1/holdings": {
            "items": [
                {
                    "symbol": "TQQQ",
                    "quantity": "3",
                    "averagePurchasePrice": "80",
                    "lastPrice": "81",
                    "currency": "USD",
                },
                {"symbol": "X", "quantity": "0"},
            ]
        },
        "GET /api/v1/buying-power": {"currency": "USD", "cashBuyingPower": "2.05"},
        "GET /api/v1/candles": {
            "candles": [
                {"timestamp": "2026-10-02T13:00:00.000+09:00", "closePrice": "749.58"},
                {"timestamp": "2026-10-01T13:00:00.000+09:00", "closePrice": "742.03"},
            ]
        },
    }
    r = _make(tmp_path, routes, seen)
    assert r.prices(["QQQ"]) == {"QQQ": Decimal("749.56")}
    assert list(r.holdings()) == ["TQQQ"]
    assert r.buying_power("USD") == Decimal("2.05")
    assert r.daily_closes("QQQ") == [
        ("2026-10-01", Decimal("742.03")),
        ("2026-10-02", Decimal("749.58")),
    ]
    assert sum(1 for q in seen if q.url.path == "/oauth2/token") == 1  # 캐시
    acct = next(q for q in seen if q.url.path == "/api/v1/holdings")
    assert (
        acct.headers["X-Tossinvest-Account"] == "1"
        and acct.headers["Authorization"] == "Bearer TOK"
    )
    price = next(q for q in seen if q.url.path == "/api/v1/prices")
    assert "X-Tossinvest-Account" not in price.headers


def test_forbidden_ip_is_explained(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    r = _make(tmp_path, {"GET /api/v1/holdings": httpx.Response(403, json={})}, seen)
    with pytest.raises(TossApiError) as info:
        r.holdings()
    assert info.value.status == 403 and "허용 IP" in str(info.value)


def test_loc_order_shape(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    r = _make(tmp_path, {"POST /api/v1/orders": {"orderId": "OID-1"}}, seen)
    res = r.order("tqqq", "BUY", 3, Decimal("83.43"), client_order_id="TQQQ-2026-10-06-1")
    assert res.accepted and res.code == "OID-1"
    body = json.loads(
        next(q for q in seen if q.method == "POST" and "orders" in q.url.path).content
    )
    assert body == {
        "clientOrderId": "TQQQ-2026-10-06-1",
        "symbol": "TQQQ",
        "side": "BUY",
        "orderType": "LIMIT",
        "timeInForce": "CLS",
        "quantity": "3",
        "price": "83.43",
    }


def test_rejection_code_is_returned(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    bad = httpx.Response(
        422,
        json={"error": {"code": "insufficient-buying-power", "message": "부족", "requestId": "r"}},
    )
    r = _make(tmp_path, {"POST /api/v1/orders": bad}, seen)
    res = r.order("QNDX", "BUY", 1, Decimal("26"), client_order_id="a")
    assert not res.accepted and res.code == "insufficient-buying-power"


def test_server_error_is_unknown_and_not_resent(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    r = _make(tmp_path, {"POST /api/v1/orders": httpx.Response(503)}, seen)
    with pytest.raises(OrderStateUnknownError):
        r.order("QNDX", "BUY", 1, Decimal("26"), client_order_id="a")
    assert sum(1 for q in seen if q.method == "POST" and "orders" in q.url.path) == 1


@pytest.mark.parametrize(
    ("enabled", "symbol", "side", "qty", "limit"),
    [
        (False, "QNDX", "BUY", 1, "26"),  # 사용자가 켜지 않음
        (True, "QQQ", "BUY", 1, "26"),  # 허용 종목 밖
        (True, "QNDX", "SELL", 1, "26"),  # QNDX 는 팔지 않는다
        (True, "QNDX", "BUY", 2, "26"),  # QNDX 는 1 주만
        (True, "TQQQ", "BUY", 61, "80"),  # TQQQ 수량 상한
        (True, "TQQQ", "BUY", 1, "250.01"),  # 가격 상한
    ],
)
def test_guards_send_nothing(
    tmp_path: Path, enabled: bool, symbol: str, side: str, qty: int, limit: str
) -> None:
    seen: list[httpx.Request] = []
    r = _make(tmp_path, {}, seen, enabled=enabled)
    with pytest.raises(TradingDisabledError):
        r.order(symbol, side, qty, Decimal(limit), client_order_id="a")
    assert seen == []


def test_reader_has_no_order_method(tmp_path: Path) -> None:
    reader = TossReader(_creds(), TossTokenCache(tmp_path / "t.json"))
    assert not hasattr(reader, "order")
    reader.close()


def test_kr_order_is_day_limit_integer_price(tmp_path: Path) -> None:
    """국내(2026-10-10): CLS 없음 → DAY 지정가, 원 단위 정수 가격, 원 상한."""
    from auto_trading.exchange.toss import is_kr_symbol

    assert is_kr_symbol("005930") and is_kr_symbol("0101N0") and not is_kr_symbol("GOOGL")
    seen: list[httpx.Request] = []
    t = TossTrader(
        _creds(),
        TossTokenCache(tmp_path / "tok.json"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                _handler({"POST /api/v1/orders": {"orderId": "OK1"}}, seen)
            )
        ),
        clock=lambda: NOW,
        sleep=lambda _s: None,
        allowed_symbols=frozenset({"005930"}),
        max_qty={"005930": 1},
        max_limit_krw=Decimal(2_000_000),
    )
    with pytest.raises(TradingDisabledError):  # 국내는 장 마감 지정가 없음
        t.order("005930", "BUY", 1, Decimal(70_400), client_order_id="a", time_in_force="CLS")
    with pytest.raises(TradingDisabledError):  # 상한
        t.order("005930", "BUY", 1, Decimal(3_000_000), client_order_id="b", time_in_force="DAY")
    res = t.order("005930", "BUY", 1, Decimal(70_400), client_order_id="c", time_in_force="DAY")
    assert res.accepted and res.code == "OK1"
    body = json.loads(seen[-1].content)
    assert body["price"] == "70400" and body["timeInForce"] == "DAY"


def test_stock_list_and_kr_calendar(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    routes = {
        "GET /api/v1/stocks/all": [
            {"symbol": "005930", "name": "삼성전자", "securityType": "STOCK"}
        ],
        "GET /api/v1/market-calendar/KR": {
            "today": {"date": "2026-10-12", "integrated": {"regularMarket": {}}}
        },
    }
    r = _make(tmp_path, routes, seen)
    assert r.stocks_all("KOSPI")[0]["name"] == "삼성전자"
    assert r.kr_open_today("2026-10-12") and not r.kr_open_today("2026-10-13")


def test_liquidation_reads_and_cancel(tmp_path: Path) -> None:
    """'전부 정리' 용: 미체결 · 판매 가능 · 미국 정규장 · 매수 호가 · 취소(관문)."""
    seen: list[httpx.Request] = []
    routes = {
        "GET /api/v1/orders": {
            "orders": [
                {
                    "orderId": "A1",
                    "symbol": "btq",
                    "side": "BUY",
                    "status": "PENDING",
                    "timeInForce": "CLS",
                    "quantity": "1",
                    "execution": {"filledQuantity": "0"},
                }
            ],
            "nextCursor": None,
            "hasNext": False,
        },
        "GET /api/v1/sellable-quantity": {"sellableQuantity": "2.5"},
        "GET /api/v1/market-calendar/US": {
            "today": {
                "date": "2026-10-12",
                "regularMarket": {
                    "startTime": "2026-10-12T22:30:00+09:00",
                    "endTime": "2026-10-13T05:00:00+09:00",
                },
            }
        },
        "GET /api/v1/orderbook": {
            "bids": [{"price": "50.1", "volume": "3"}, {"price": "0", "volume": "1"}],
            "asks": [{"price": "50.4", "volume": "2"}, {"price": "50.2", "volume": "5"}],
        },
        "POST /api/v1/orders/A1/cancel": {"orderId": "C1"},
    }
    t = _make(tmp_path, routes, seen)
    o = t.open_orders()[0]
    assert (o.order_id, o.symbol, o.side) == ("A1", "BTQ", "BUY")
    assert (o.qty, o.filled_qty) == (Decimal(1), Decimal(0))
    assert seen[-1].url.params["status"] == "OPEN"
    assert t.sellable("TQQQ") == Decimal("2.5")
    assert t.us_regular_session("2026-10-12") == (
        "2026-10-12T22:30:00+09:00",
        "2026-10-13T05:00:00+09:00",
    )
    assert t.us_regular_session("2026-10-13") is None  # 날짜가 다르면(휴장 등) 없음
    assert t.bids("TQQQ") == [(Decimal("50.1"), Decimal("3"))]
    assert t.asks("TQQQ") == [(Decimal("50.2"), Decimal("5")), (Decimal("50.4"), Decimal("2"))]
    res = t.cancel("A1")
    assert res.accepted and res.code == "C1"
    assert seen[-1].headers["X-Tossinvest-Account"] == "1"
    with pytest.raises(TradingDisabledError):
        _make(tmp_path, routes, [], enabled=False).cancel("A1")
    bad = _make(tmp_path, {"POST /api/v1/orders/A1/cancel": httpx.Response(503)}, [])
    with pytest.raises(OrderStateUnknownError):
        bad.cancel("A1")


def test_under_one_dollar_price_has_four_decimals(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    t = _make(tmp_path, {"POST /api/v1/orders": {"orderId": "X"}}, seen)
    t.order("TQQQ", "SELL", 1, Decimal("0.9876"), client_order_id="c", time_in_force="DAY")
    body = json.loads(seen[-1].content)
    assert body["price"] == "0.9876" and body["timeInForce"] == "DAY"
