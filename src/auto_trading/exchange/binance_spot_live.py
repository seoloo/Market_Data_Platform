"""Binance 현물 **실계좌** 주문 어댑터 — Vega(T/v2 · BTC · ETH · SOL) 전용 (2026-10-15).

사용자 (2026-10-15): "cruise는 그냥 바이낸스로 옮기면 돼" — 업비트(원화) Vega 를
바이낸스(USDT)로 옮긴다.
문서 85 §11 의 L3(실계좌 현물 어댑터)에 해당한다.

* 주문은 **지정가 IOC 만**(사용자 규칙 2026-10-05: 시장가 금지). 체결 · 주문 조회를 더한다.
* 키는 읽기 전용 키와 따로: ``BINANCE_LIVE_API_KEY`` · ``BINANCE_LIVE_API_SECRET``
  (현물 거래 · 읽기만, 출금 금지, 집 IP 제한 — 사용자가 만든다).
  ``BINANCE_LIVE_TRADING_ENABLED=true`` 일 때만 주문을 보낸다(:func:`load_live_spot`).
* 전송 결과 분류는 데모 어댑터와 같다: 거래소가 읽고 거절 = :class:`OrderRejectedError`,
  네트워크 · 5xx · 레이트 리밋 = :class:`OrderStateUnknownError` (같은 주문 번호로 조회해 해소).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from auto_trading.config.secrets import BinanceCredentials, read_env_file
from auto_trading.core.errors import ConfigError, OrderRejectedError, OrderStateUnknownError
from auto_trading.exchange.binance_spot import BASE_URL, BinanceSpotReadOnly, sign
from auto_trading.exchange.spot import OrderStatus, SpotOrderResult
from auto_trading.exchange.wallet import TransferDirection, TransferRow

__all__ = ["BinanceSpotLive", "LiveFill", "load_live_spot"]

_RECV_WINDOW_MS = 5000
_UNKNOWN_CODES = frozenset({-1007})
FUTURES_URL = "https://fapi.binance.com"


def _num(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class LiveFill:
    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    commission: Decimal
    commission_asset: str


def load_live_spot(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> tuple[BinanceCredentials, bool]:
    """(거래 키, 주문 허용). 키가 없으면 ConfigError — 값은 메시지에 넣지 않는다."""
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    key = merged.get("BINANCE_LIVE_API_KEY", "").strip()
    secret = merged.get("BINANCE_LIVE_API_SECRET", "").strip()
    if not key or not secret:
        raise ConfigError(
            "BINANCE_LIVE_API_KEY / BINANCE_LIVE_API_SECRET 가 .env 에 없다"
            " (거래 키는 사용자가 만든다)"
        )
    enabled = merged.get("BINANCE_LIVE_TRADING_ENABLED", "").strip().lower() == "true"
    return BinanceCredentials(key, secret), enabled


class BinanceSpotLive(BinanceSpotReadOnly):
    __slots__ = ("trading_enabled",)

    def __init__(
        self,
        credentials: BinanceCredentials,
        *,
        trading_enabled: bool,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        clock: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(credentials, client=client, base_url=base_url, clock=clock)
        self.trading_enabled = trading_enabled

    # ------------------------------------------------------------ 전송

    def _send(self, method: str, path: str, params: Mapping[str, Any]) -> Any:
        assert self._creds is not None
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
        text = f"Binance 오류 {response.status_code} code={code}: {msg} ({path})"
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

    # ------------------------------------------------------------ 규칙

    def tick_size(self, symbol: str) -> Decimal:
        data = self._request("/api/v3/exchangeInfo", {"symbol": symbol})
        (info,) = data["symbols"]
        price = next(f for f in info["filters"] if f["filterType"] == "PRICE_FILTER")
        return Decimal(price["tickSize"]).normalize()

    # ------------------------------------------------------------ 주문 (지정가 IOC 만)

    def place_limit_ioc(
        self, symbol: str, side: str, quantity: Decimal, price: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        if not self.trading_enabled:
            raise ConfigError("BINANCE_LIVE_TRADING_ENABLED=true 가 아니라 주문하지 않는다")
        if side not in ("BUY", "SELL") or quantity <= 0 or price <= 0:
            raise ValueError(f"잘못된 주문: {side} {symbol} {quantity}@{price}")
        data = self._send(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "IOC",
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
        raise ConfigError("Vega 는 시장가를 쓰지 않는다 (사용자 규칙 2026-10-05)")

    def fills(self, symbol: str, exchange_order_id: str) -> list[LiveFill]:
        rows = self._send(
            "GET", "/api/v3/myTrades", {"symbol": symbol, "orderId": exchange_order_id}
        )
        return [
            LiveFill(
                price=Decimal(r["price"]),
                qty=Decimal(r["qty"]),
                quote_qty=Decimal(r["quoteQty"]),
                commission=Decimal(r["commission"]),
                commission_asset=str(r["commissionAsset"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------ 지갑 이체 (Altair 자동 분배)
    # 사용자 2026-10-15 "비율 대로 자동 분배 할 수 있도록 해줘" — 문서 53 의 WalletTransfers 계약을
    # 실계좌로 구현한다. 키에 "Permits Universal Transfer" 권한이 필요하다. 출금 권한은 쓰지 않는다.

    def transfer(self, direction: TransferDirection, asset: str, amount: Decimal) -> str:
        """현물 ↔ USDⓈ-M 선물 지갑 이체 → tranId.

        거부면 OrderRejectedError, 결과를 모르면 OrderStateUnknownError."""
        if not self.trading_enabled:
            raise ConfigError("BINANCE_LIVE_TRADING_ENABLED=true 가 아니라 이체하지 않는다")
        body = self._send(
            "POST",
            "/sapi/v1/asset/transfer",
            {"type": direction.value, "asset": asset, "amount": _num(amount)},
        )
        return str(body["tranId"])

    def transfers(
        self, direction: TransferDirection, start_ms: int, end_ms: int
    ) -> list[TransferRow]:
        body = self._send(
            "GET",
            "/sapi/v1/asset/transfer",
            {"type": direction.value, "startTime": start_ms, "endTime": end_ms, "size": 100},
        )
        return [
            TransferRow(
                tran_id=str(r["tranId"]),
                direction=direction,
                asset=str(r["asset"]),
                amount=Decimal(str(r["amount"])),
                status=str(r.get("status", "")),
                timestamp_ms=int(r["timestamp"]),
            )
            for r in body.get("rows") or []
        ]

    def futures_balance(self, asset: str = "USDT") -> Decimal:
        """USDⓈ-M 선물 지갑의 ``asset`` 잔고(지갑 잔고, 미실현 손익 제외). 읽기만."""
        assert self._creds is not None
        query = {"recvWindow": _RECV_WINDOW_MS, "timestamp": self._clock() + self.clock_offset_ms()}
        encoded = urlencode(query)
        sig = sign(self._creds.api_secret, encoded)
        url = f"{FUTURES_URL}/fapi/v2/balance?{encoded}&signature={sig}"
        try:
            r = self._client.get(url, headers={"X-MBX-APIKEY": self._creds.api_key})
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(f"선물 잔고 조회 실패 ({type(exc).__name__})") from None
        if not r.is_success:
            raise OrderRejectedError(f"선물 잔고 조회 실패 {r.status_code}")
        for row in r.json():
            if row.get("asset") == asset:
                return Decimal(str(row.get("balance", "0")))
        return Decimal(0)
