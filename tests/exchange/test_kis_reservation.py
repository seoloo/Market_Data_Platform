"""KIS 예약 매수 1주 전용 경로 (문서 89) — 가짜 서버로만. 실제 주문 없음."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.config.secrets import KisCredentials
from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.exchange.kis import KisReservationBuyer, KisTokenCache
from auto_trading.exchange.overseas import TradingDisabledError

KEY, SECRET, TOKEN = "APPKEY-abcdef", "APPSECRET-xyz", "TOKEN-123"
NOW = 1_790_000_000_000
PATH = "/uapi/overseas-stock/v1/trading/order-resv"


def make(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], *, enabled: bool = True
) -> KisReservationBuyer:
    cache = KisTokenCache(tmp_path / "kis_token.json")
    cache.save(KEY, TOKEN, NOW + 86_400_000)
    return KisReservationBuyer(
        KisCredentials(KEY, SECRET, "12345678", "01", enabled),
        cache,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
        sleep=lambda _s: None,
        allowed_symbols=frozenset({"QNDX"}),
        max_limit_usd=Decimal("40"),
    )


def ok(output: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={"rt_cd": "0", "msg_cd": "APBK0013", "msg1": "주문 전송 완료", "output": output or {}},
    )


def test_accepted_request_shape(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok({"ODNO": "0000123"})

    result = make(tmp_path, handler).reserve_buy("qndx", "NASD", 1, Decimal("25.97"))
    assert result.accepted and result.output["ODNO"] == "0000123"
    (req,) = seen
    assert req.method == "POST" and req.url.path == PATH
    assert req.headers["tr_id"] == "TTTT3014U"
    body = json.loads(req.content)
    assert body == {
        "CANO": "12345678",
        "ACNT_PRDT_CD": "01",
        "PDNO": "QNDX",
        "OVRS_EXCG_CD": "NASD",
        "FT_ORD_QTY": "1",
        "FT_ORD_UNPR3": "25.97",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",
    }


@pytest.mark.parametrize(
    ("enabled", "symbol", "qty", "limit"),
    [
        (False, "QNDX", 1, "25"),  # 사용자가 켜지 않음
        (True, "QQQ", 1, "25"),  # 허용 종목 밖
        (True, "QNDX", 2, "25"),  # 1 주만
        (True, "QNDX", 1, "40.01"),  # 가격 상한 초과
        (True, "QNDX", 1, "0"),
    ],
)
def test_guards_send_nothing(
    tmp_path: Path, enabled: bool, symbol: str, qty: int, limit: str
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok()

    buyer = make(tmp_path, handler, enabled=enabled)
    with pytest.raises(TradingDisabledError):
        buyer.reserve_buy(symbol, "NASD", qty, Decimal(limit))
    assert calls == []


def test_network_error_is_unknown_and_never_resent(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(OrderStateUnknownError):
        make(tmp_path, handler).reserve_buy("QNDX", "NASD", 1, Decimal("25"))
    assert len(calls) == 1


def test_rejection_is_returned_once_and_token_error_is_retried_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def reject(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "APBK1234", "msg1": "주문 불가"})

    result = make(tmp_path, reject).reserve_buy("QNDX", "NASD", 1, Decimal("25"))
    assert not result.accepted and result.code == "APBK1234" and calls == [PATH]

    order_calls: list[str] = []

    def token_then_ok(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "NEW", "expires_in": 86400})
        order_calls.append(request.headers["authorization"])
        if len(order_calls) == 1:
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"})
        return ok()

    assert (
        make(tmp_path / "b", token_then_ok).reserve_buy("QNDX", "NASD", 1, Decimal("25")).accepted
    )
    assert order_calls == [f"Bearer {TOKEN}", "Bearer NEW"]


def test_read_only_class_still_refuses_orders(tmp_path: Path) -> None:
    from auto_trading.exchange.kis import KisOverseasReadOnly

    ro = KisOverseasReadOnly(
        KisCredentials(KEY, SECRET, "12345678", "01", True),
        KisTokenCache(tmp_path / "t.json"),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: ok())),
    )
    with pytest.raises(TradingDisabledError):
        ro.place_order("QNDX", 1)


def test_buying_power_counts_integrated_margin(tmp_path: Path) -> None:
    """통합증거금 계좌: 외화 기준 0 이어도 원화 포함 최대 수량으로 본다 (2026-09-26 실측 모양)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/inquire-psamount")
        assert request.headers["tr_id"] == "TTTS3007R"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output": {
                    "max_ord_psbl_qty": "0",
                    "ovrs_ord_psbl_amt": "0.00",
                    "ovrs_max_ord_psbl_qty": "7",
                    "frcr_ord_psbl_amt1": "207.375669",
                    "exrt": "1360.0000000000",
                },
            },
        )

    bp = make(tmp_path, handler).buying_power("QNDX", "NASD", Decimal("25.97"))
    assert bp.max_qty == Decimal("7") and bp.usd_available == Decimal("207.375669")


def test_read_retries_gateway_error_but_order_is_not_resent(tmp_path: Path) -> None:
    """EGW00300(HTTP 500)은 조회만 다시 묻는다. 주문 POST 는 거절 응답으로 한 번만 돌려준다."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) < 3:
            return httpx.Response(
                500, json={"rt_cd": "1", "msg_cd": "EGW00300", "msg1": "Gateway 라우팅 오류"}
            )
        if request.url.path == PATH:
            return ok()
        return httpx.Response(200, json={"rt_cd": "0", "output": {"max_ord_psbl_qty": "2"}})

    buyer = make(tmp_path, handler)
    assert buyer.buying_power("QNDX", "NASD", Decimal("25")).max_qty == Decimal("2")
    assert len(calls) == 3  # 두 번 실패 뒤 세 번째 성공

    order_calls: list[str] = []

    def order_500(request: httpx.Request) -> httpx.Response:
        order_calls.append(request.url.path)
        return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00300", "msg1": "오류"})

    with pytest.raises(OrderStateUnknownError):  # 접수 여부 모름 → 다음 날 잔고로 확인
        make(tmp_path / "o", order_500).reserve_buy("QNDX", "NASD", 1, Decimal("25"))
    assert order_calls == [PATH]


def _qs_trader(tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    cache = KisTokenCache(tmp_path / "kis_token.json")
    cache.save(KEY, TOKEN, NOW + 86_400_000)
    return KisReservationBuyer(
        KisCredentials(KEY, SECRET, "12345678", "01", True),
        cache,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
        sleep=lambda _s: None,
        allowed_symbols=frozenset({"QNDX", "TQQQ"}),
        max_limit_usd=Decimal("250"),
        max_qty={"TQQQ": 60},
        sell_symbols=frozenset({"TQQQ"}),
    )


def test_qs_tqqq_sell_shape_and_multi_qty_buy(tmp_path: Path) -> None:
    """문서 118: TQQQ 만 여러 주 매수·예약 매도(TTTT3016U). 몸통은 매수와 같다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok()

    trader = _qs_trader(tmp_path, handler)
    assert trader.reserve_sell("tqqq", "NASD", 3, Decimal("78.57")).accepted
    assert trader.reserve_buy("TQQQ", "NASD", 60, Decimal("83.43")).accepted
    sell, buy = seen
    assert sell.headers["tr_id"] == "TTTT3016U" and buy.headers["tr_id"] == "TTTT3014U"
    assert json.loads(sell.content)["FT_ORD_QTY"] == "3"
    assert json.loads(sell.content)["PDNO"] == "TQQQ"


@pytest.mark.parametrize(
    ("side", "symbol", "qty"),
    [
        ("sell", "QNDX", 1),  # QNDX 는 팔지 않는다
        ("buy", "QNDX", 2),  # QNDX 는 여전히 1 주만
        ("buy", "TQQQ", 61),  # TQQQ 수량 상한
        ("sell", "TQQQ", 0),
    ],
)
def test_qs_guards_send_nothing(tmp_path: Path, side: str, symbol: str, qty: int) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok()

    trader = _qs_trader(tmp_path, handler)
    send = trader.reserve_sell if side == "sell" else trader.reserve_buy
    with pytest.raises(TradingDisabledError):
        send(symbol, "NASD", qty, Decimal("50"))
    assert calls == []


def test_default_buyer_cannot_sell(tmp_path: Path) -> None:
    buyer = make(tmp_path, lambda r: ok())
    with pytest.raises(TradingDisabledError):
        buyer.reserve_sell("QNDX", "NASD", 1, Decimal("25"))
