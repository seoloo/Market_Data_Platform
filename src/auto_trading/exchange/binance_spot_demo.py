"""Binance 현물 **데모 모드 전용** 주문 어댑터 (docs/research/55). 가짜 돈만 움직인다.

읽기는 :class:`BinanceSpotReadOnly` 를 그대로 쓰고, post-only 지정가·시장가·취소·체결 조회만 더한다.
**주소를 데모 모드에 묶는다** — 생성 시 ``https://demo-api.binance.com`` 이 아니면 거부한다.
실계좌(api.binance.com) 현물 주문 경로는 Phase 8 승인 전까지 존재하지 않는다.

데모 모드: 가격·호가가 실거래소와 비슷하고 필터·한도는 같다(공식 문서, 2026-09-25 확인).
키는 선물 데모(``demo-fapi``)와 같은 데모 키(``BINANCE_TESTNET_API_*``)를 쓴다
(2026-09-25 잔고 조회로 확인).

전송 결과 분류는 선물 테스트넷 어댑터와 같다.

| 거래소 반응 | 분류 |
|---|---|
| 4xx + Binance 오류 코드 | :class:`OrderRejectedError` — 거래소가 읽고 거절 |
| 네트워크·5xx·-1007·레이트 리밋 | :class:`OrderStateUnknownError` — 조회로 해소 |
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import (
    ConfigError,
    OrderRejectedError,
    OrderStateUnknownError,
)
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly, sign
from auto_trading.exchange.spot import OrderStatus, SpotOrderResult

__all__ = ["DEMO_URL", "BinanceSpotDemo", "SpotFill"]

DEMO_URL = "https://demo-api.binance.com"
_RECV_WINDOW_MS = 5000
_UNKNOWN_CODES = frozenset({-1007})


def _num(value: Decimal) -> str:
    """지수 표기 없이."""
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class SpotFill:
    """체결 하나 (myTrades). 수수료는 체결마다 자산이 다를 수 있다."""

    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    commission: Decimal
    commission_asset: str
    is_maker: bool


class BinanceSpotDemo(BinanceSpotReadOnly):
    __slots__ = ()

    def __init__(
        self,
        credentials: BinanceCredentials,
        *,
        client: httpx.Client | None = None,
        base_url: str = DEMO_URL,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if base_url.rstrip("/") != DEMO_URL:
            raise ConfigError(
                f"데모 전용 어댑터에 데모가 아닌 주소: {base_url} — 실계좌 현물 주문 경로는 없다"
            )
        super().__init__(credentials, client=client, base_url=base_url, clock=clock)

    # ------------------------------------------------------------ 전송

    def _send(self, method: str, path: str, params: Mapping[str, Any]) -> Any:
        assert self._creds is not None  # 생성자가 키를 요구한다
        query = dict(params)
        query["recvWindow"] = _RECV_WINDOW_MS
        query["timestamp"] = self._clock() + self.clock_offset_ms()
        encoded = urlencode(query)
        url = f"{self.base_url}{path}?{encoded}&signature={sign(self._creds.api_secret, encoded)}"
        headers = {"X-MBX-APIKEY": self._creds.api_key}
        try:
            response = self._client.request(method, url, headers=headers)
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(f"전송 결과 모름: {path} ({type(exc).__name__})") from None
        if response.status_code in (418, 429):
            raise OrderStateUnknownError(f"레이트 리밋 {response.status_code} — 전송 결과 모름")
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        if response.is_success:
            return body
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("msg", "") if isinstance(body, dict) else ""
        text = f"Binance 데모 오류 {response.status_code} code={code}: {msg} ({path})"
        if code is None or code in _UNKNOWN_CODES or response.status_code >= 500:
            raise OrderStateUnknownError(f"전송 결과 모름: {text}")
        raise OrderRejectedError(text)

    @staticmethod
    def _result(data: Mapping[str, Any]) -> SpotOrderResult:
        return SpotOrderResult(
            client_order_id=data["clientOrderId"],
            exchange_order_id=str(data["orderId"]),
            symbol=data["symbol"],
            side=data["side"],
            status=OrderStatus(data["status"]),
            orig_qty=Decimal(data["origQty"]),
            executed_qty=Decimal(data["executedQty"]),
            quote_qty=Decimal(data["cummulativeQuoteQty"]),
            fee=Decimal(0),
            fee_asset="",
        )

    # ------------------------------------------------------------ 주문

    def place_limit_maker(
        self, symbol: str, side: str, quantity: Decimal, price: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        """post-only 지정가. 걸자마자 체결될 가격이면 거래소가 거절한다(-2010)."""
        data = self._send(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT_MAKER",
                "quantity": _num(quantity),
                "price": _num(price),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
        )
        return self._result(data)

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        data = self._send(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": _num(quantity),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
        )
        return self._result(data)

    def cancel_order(self, symbol: str, client_order_id: str) -> SpotOrderResult:
        data = self._send(
            "DELETE", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}
        )
        return self._result(data)

    def fills(self, symbol: str, exchange_order_id: str) -> list[SpotFill]:
        rows = self._send(
            "GET", "/api/v3/myTrades", {"symbol": symbol, "orderId": exchange_order_id}
        )
        return [
            SpotFill(
                price=Decimal(r["price"]),
                qty=Decimal(r["qty"]),
                quote_qty=Decimal(r["quoteQty"]),
                commission=Decimal(r["commission"]),
                commission_asset=str(r["commissionAsset"]),
                is_maker=bool(r["isMaker"]),
            )
            for r in rows
        ]
