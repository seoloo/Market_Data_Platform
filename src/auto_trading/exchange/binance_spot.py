"""Binance 현물 **읽기 전용** 어댑터 (T/v2 Phase 5-4).

잔고·주문 규칙·가격·주문 조회·키 권한 확인만 한다. 주문 전송은 이 클래스에서 항상 거부한다 —
Phase 8(소액 실거래) 승인 전에는 주문 경로가 존재하지 않아야 한다.
서명: HMAC-SHA256(secret, query string), 헤더 ``X-MBX-APIKEY``. 시계 차이는 서버 시간으로 보정한다.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import ExchangeError, RateLimitError
from auto_trading.core.types import Candle
from auto_trading.exchange.spot import OrderStatus, SpotFilters, SpotOrderResult

__all__ = ["BASE_URL", "BinanceSpotReadOnly", "sign"]

BASE_URL = "https://api.binance.com"
_RECV_WINDOW_MS = 5000
_ORDER_NOT_FOUND = -2013


def sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class BinanceSpotReadOnly:
    __slots__ = ("_client", "_clock", "_creds", "_offset_ms", "base_url")

    def __init__(
        self,
        credentials: BinanceCredentials | None,
        *,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._creds = credentials
        self._client = client if client is not None else httpx.Client(timeout=10.0)
        self.base_url = base_url.rstrip("/")
        self._clock = clock if clock is not None else lambda: int(time.time() * 1000)
        self._offset_ms: int | None = None

    # ------------------------------------------------------------ HTTP

    def _request(
        self, path: str, params: Mapping[str, Any] | None = None, *, signed: bool = False
    ) -> Any:
        try:
            return self._request_once(path, params, signed=signed)
        except ExchangeError as exc:
            # -1021 timestamp 오류: 잰 시간 차이가 낡았다 — 다시 재고 한 번만 다시 (조회 전용 경로)
            if not signed or "code=-1021" not in str(exc):
                raise
            self._offset_ms = None
            return self._request_once(path, params, signed=signed)

    def _request_once(self, path: str, params: Mapping[str, Any] | None, *, signed: bool) -> Any:
        query = dict(params or {})
        headers = {}
        if signed:
            if self._creds is None:
                raise ExchangeError(f"서명 요청에 API 키가 필요하다: {path}")
            query["recvWindow"] = _RECV_WINDOW_MS
            query["timestamp"] = self._clock() + self.clock_offset_ms()
            encoded = urlencode(query)
            query_string = f"{encoded}&signature={sign(self._creds.api_secret, encoded)}"
            headers["X-MBX-APIKEY"] = self._creds.api_key
        else:
            query_string = urlencode(query)
        url = f"{self.base_url}{path}" + (f"?{query_string}" if query_string else "")
        try:
            response = self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ExchangeError(f"Binance 요청 실패: {path} ({type(exc).__name__})") from exc
        if response.status_code in (418, 429):
            raise RateLimitError(f"Binance 레이트 리밋 {response.status_code}: {path}")
        body = response.json() if response.content else {}
        if response.is_success:
            return body
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("msg") if isinstance(body, dict) else ""
        raise ExchangeError(f"Binance 오류 {response.status_code} code={code}: {msg} ({path})")

    def clock_offset_ms(self) -> int:
        """서버 시간 − 로컬 시간. 한 번 재고 재사용한다 (timestamp 오류 -1021 방지)."""
        if self._offset_ms is None:
            before = self._clock()
            server = int(self._request("/api/v3/time")["serverTime"])
            after = self._clock()
            self._offset_ms = server - (before + after) // 2
        return self._offset_ms

    # ------------------------------------------------------------ 조회

    def api_restrictions(self) -> dict[str, bool]:
        """키 권한. 읽기 전용·IP 제한 확인에 쓴다."""
        data = self._request("/sapi/v1/account/apiRestrictions", signed=True)
        return {k: bool(v) for k, v in data.items() if isinstance(v, bool)}

    def balances(self) -> dict[str, Decimal]:
        data = self._request("/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
        out: dict[str, Decimal] = {}
        for item in data.get("balances", []):
            total = Decimal(item["free"]) + Decimal(item["locked"])
            if total:
                out[item["asset"]] = total
        return out

    def filters(self, symbol: str) -> SpotFilters:
        data = self._request("/api/v3/exchangeInfo", {"symbol": symbol})
        (info,) = data["symbols"]
        by_type = {f["filterType"]: f for f in info["filters"]}
        lot = by_type["LOT_SIZE"]
        notional = by_type.get("NOTIONAL") or by_type["MIN_NOTIONAL"]
        return SpotFilters(
            step_size=Decimal(lot["stepSize"]).normalize(),
            min_qty=Decimal(lot["minQty"]).normalize(),
            min_notional=Decimal(notional["minNotional"]).normalize(),
        )

    def last_price(self, symbol: str) -> Decimal:
        return Decimal(self._request("/api/v3/ticker/price", {"symbol": symbol})["price"])

    def order_book(
        self, symbol: str, limit: int = 100
    ) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        """호가 (매수 호가, 매도 호가) — 각 (가격, 수량), 좋은 가격부터. 공개 API, 키 불필요."""
        data = self._request("/api/v3/depth", {"symbol": symbol, "limit": limit})
        bids = [(Decimal(p), Decimal(q)) for p, q in data["bids"]]
        asks = [(Decimal(p), Decimal(q)) for p, q in data["asks"]]
        return bids, asks

    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]:
        """``as_of_ms`` 까지 **마감된** 일봉 (공개 API, 키 불필요). 진행 중인 봉은 버린다."""
        rows = self._request(
            "/api/v3/klines",
            {"symbol": symbol, "interval": "1d", "endTime": as_of_ms, "limit": limit},
        )
        out = []
        for row in rows:
            open_ms, close_ms = int(row[0]), int(row[6])
            if close_ms > as_of_ms or close_ms != open_ms + Timeframe.D1.ms - 1:
                continue
            out.append(
                Candle(
                    open_ms=open_ms,
                    close_ms=close_ms,
                    open=Decimal(row[1]),
                    high=Decimal(row[2]),
                    low=Decimal(row[3]),
                    close=Decimal(row[4]),
                    volume=Decimal(row[5]),
                    quote_volume=Decimal(row[7]),
                    trade_count=int(row[8]),
                )
            )
        return out

    def get_order(self, symbol: str, client_order_id: str) -> SpotOrderResult | None:
        try:
            data = self._request(
                "/api/v3/order",
                {"symbol": symbol, "origClientOrderId": client_order_id},
                signed=True,
            )
        except ExchangeError as exc:
            if f"code={_ORDER_NOT_FOUND}" in str(exc):
                return None
            raise
        # 수수료는 체결 조회(myTrades)로만 알 수 있다 — Paper/실거래 단계에서 추가한다.
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

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        raise ExchangeError("읽기 전용 어댑터는 주문을 보내지 않는다 (Phase 8 승인 전)")

    def close(self) -> None:
        self._client.close()
