"""Binance USD-M 선물 **서명 읽기 전용** 어댑터 (Phase 5 선물).

계좌·포지션·거래 규칙·mark 가격·주문/보호 스톱 조회만 한다.
주문·취소는 이 클래스에서 **항상 거부**한다.
Phase 8(소액 실거래) 승인 전에는 선물 주문 경로가 존재하지 않아야 한다.

엔드포인트는 2026-09-24 공식 문서로 확인했다 (OPERATIONS: 구현 시 공식 문서 재확인).

| 용도 | 엔드포인트 |
|---|---|
| 서버 시각 | ``GET /fapi/v1/time`` |
| 거래 규칙 | ``GET /fapi/v1/exchangeInfo`` (MARKET_LOT_SIZE·PRICE_FILTER·MIN_NOTIONAL) |
| 계좌·포지션 | ``GET /fapi/v2/account`` — v3 에는 포지션 ``leverage``·``entryPrice`` 가 없다 |
| mark 가격 | ``GET /fapi/v1/premiumIndex`` |
| 일반 주문 | ``GET /fapi/v1/order``(origClientOrderId) · ``GET /fapi/v1/openOrders`` |
| 보호 스톱 | ``GET /fapi/v1/algoOrder``(clientAlgoId) · ``GET /fapi/v1/openAlgoOrders`` |

보호 스톱(STOP_MARKET)은 2025-12-09 부터 Algo Order API 로 옮겨졌다
(:mod:`auto_trading.exchange.futures`).
발동한 스톱은 ``actualOrderId`` 로 실제 시장가 주문을 다시 조회해 체결 수량·가격을 얻는다.

**실계좌 읽기 전용 점검으로 확인한 것 (2026-09-24)** — 공식 문서와 다른 점이 있었다:

* 걸린 Algo 주문 목록은 ``/fapi/v1/openAlgoOrders`` 다. 엔드포인트 문서 페이지의
  ``/fapi/v1/algoOpenOrders`` 는 **404** 이고, 변경 기록의 표기가 맞았다.
* 없는 Algo 주문 조회도 일반 주문과 같은 ``-2013 Order does not exist`` 를 준다 (문서에 없던 코드).

"없음"으로 보는 오류는 ``-2013`` 하나뿐이다. 그 밖의 오류는 전부 예외로 올려 호출자가
**결과를 모르는 상태**로 다루게 한다 — 모르는 것을 "없다"로 읽어 NOT_PLACED 로 닫으면
실제로 걸린 주문을 놓친다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import ExchangeError, OrderRejectedError, RateLimitError
from auto_trading.exchange.binance_spot import sign
from auto_trading.exchange.futures import (
    FuturesAccount,
    FuturesFilters,
    FuturesOrderResult,
    FuturesOrderType,
    FuturesPosition,
    OrderStatus,
)

__all__ = ["BASE_URL", "BinanceUsdmReadOnly", "OrderPathDisabledError"]

BASE_URL = "https://fapi.binance.com"
_RECV_WINDOW_MS = 5000
_ORDER_NOT_FOUND = -2013
_ZERO = Decimal(0)
_STATUS = {
    "NEW": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
    "EXPIRED_IN_MATCH": OrderStatus.EXPIRED,
}
_ALGO_TRIGGERED = frozenset({"TRIGGERED", "FINISHED"})


class OrderPathDisabledError(OrderRejectedError):
    """읽기 전용 어댑터에 주문·취소를 요청했다.

    결과를 모르는 상태가 아니라 **확실히 보내지 않았다.** 그래서 OrderRejectedError 로 둔다 —
    주문 관리자가 UNKNOWN 이 아니라 REJECTED 로 기록하고 신규 진입을 헛되이 막지 않게.
    """


class _ApiError(ExchangeError):
    def __init__(self, message: str, code: int | None) -> None:
        super().__init__(message)
        self.code = code


def _dec(value: object) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else _ZERO


class BinanceUsdmReadOnly:
    __slots__ = ("_client", "_clock", "_creds", "_filters", "_offset_ms", "base_url")

    def __init__(
        self,
        credentials: BinanceCredentials,
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
        self._filters: dict[str, FuturesFilters] | None = None

    # ------------------------------------------------------------ HTTP

    def _request(
        self, path: str, params: Mapping[str, Any] | None = None, *, signed: bool = False
    ) -> Any:
        query = dict(params or {})
        headers = {}
        if signed:
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
            raise ExchangeError(f"Binance 선물 요청 실패: {path} ({type(exc).__name__})") from None
        if response.status_code in (418, 429):
            raise RateLimitError(f"Binance 선물 레이트 리밋 {response.status_code}: {path}")
        try:
            body = response.json() if response.content else {}
        except ValueError:
            # 없는 경로(404 HTML 등). 계약대로 ExchangeError 로 올린다 — 파싱 오류로 죽지 않는다.
            raise _ApiError(
                f"Binance 선물 응답이 JSON 이 아니다 {response.status_code} ({path})", None
            ) from None
        if response.is_success:
            return body
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("msg") if isinstance(body, dict) else ""
        raise _ApiError(
            f"Binance 선물 오류 {response.status_code} code={code}: {msg} ({path})", code
        )

    def clock_offset_ms(self) -> int:
        """서버 시간 − 로컬 시간. 한 번 재고 재사용한다 (timestamp 오류 -1021 방지)."""
        if self._offset_ms is None:
            before = self._clock()
            server = int(self._request("/fapi/v1/time")["serverTime"])
            after = self._clock()
            self._offset_ms = server - (before + after) // 2
        return self._offset_ms

    # ------------------------------------------------------------ 조회

    def filters(self, symbol: str) -> FuturesFilters:
        if self._filters is None:
            data = self._request("/fapi/v1/exchangeInfo")
            out: dict[str, FuturesFilters] = {}
            for info in data.get("symbols", []):
                by_type = {f["filterType"]: f for f in info.get("filters", [])}
                lot = by_type.get("MARKET_LOT_SIZE") or by_type.get("LOT_SIZE")
                if lot is None or "PRICE_FILTER" not in by_type:
                    continue
                notional = by_type.get("MIN_NOTIONAL", {}).get("notional", "0")
                out[info["symbol"]] = FuturesFilters(
                    step_size=_dec(lot["stepSize"]).normalize(),
                    min_qty=_dec(lot["minQty"]).normalize(),
                    min_notional=_dec(notional).normalize(),
                    tick_size=_dec(by_type["PRICE_FILTER"]["tickSize"]).normalize(),
                )
            self._filters = out
        if symbol not in self._filters:
            raise ExchangeError(f"거래 규칙에 없는 심볼: {symbol}")
        return self._filters[symbol]

    def account(self) -> FuturesAccount:
        data = self._request("/fapi/v2/account", signed=True)
        positions = {}
        for p in data.get("positions", []):
            qty = _dec(p.get("positionAmt"))
            if qty:
                positions[p["symbol"]] = FuturesPosition(
                    p["symbol"], qty, _dec(p.get("entryPrice")), int(p.get("leverage", 0))
                )
        return FuturesAccount(
            wallet_balance=_dec(data.get("totalWalletBalance")),
            available_balance=_dec(data.get("availableBalance")),
            positions=dict(sorted(positions.items())),
        )

    def mark_price(self, symbol: str) -> Decimal:
        return _dec(self._request("/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

    def get_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        try:
            data = self._request(
                "/fapi/v1/order",
                {"symbol": symbol, "origClientOrderId": client_order_id},
                signed=True,
            )
        except _ApiError as exc:
            if exc.code == _ORDER_NOT_FOUND:
                return None
            raise
        return self._order(data)

    def open_orders(self, symbol: str) -> list[FuturesOrderResult]:
        rows = self._request("/fapi/v1/openOrders", {"symbol": symbol}, signed=True)
        return [self._order(r) for r in rows]

    def get_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        try:
            data = self._request(
                "/fapi/v1/algoOrder", {"clientAlgoId": client_order_id}, signed=True
            )
        except _ApiError as exc:
            if exc.code == _ORDER_NOT_FOUND:
                return None
            raise
        if not data or data.get("symbol") != symbol:
            return None
        return self._stop(data)

    def open_stops(self, symbol: str) -> list[FuturesOrderResult]:
        rows = self._request("/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True)
        return [self._stop(r) for r in rows]

    # ------------------------------------------------------------ 주문 (항상 거부)

    def _refuse(self) -> FuturesOrderResult:
        raise OrderPathDisabledError(
            "읽기 전용 어댑터 — Phase 8(소액 실거래) 승인 전에는 선물 주문 경로가 없다"
        )

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str, *, reduce_only: bool
    ) -> FuturesOrderResult:
        return self._refuse()

    def place_stop_market(
        self, symbol: str, side: str, stop_price: Decimal, client_order_id: str
    ) -> FuturesOrderResult:
        return self._refuse()

    def cancel_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self._refuse()

    def cancel_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self._refuse()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------ 변환

    @staticmethod
    def _order(d: Mapping[str, Any]) -> FuturesOrderResult:
        return FuturesOrderResult(
            client_order_id=str(d.get("clientOrderId", "")),
            exchange_order_id=str(d.get("orderId", "")),
            symbol=str(d["symbol"]),
            side=str(d["side"]),
            order_type=FuturesOrderType.MARKET,
            status=_STATUS.get(str(d.get("status")), OrderStatus.NEW),
            orig_qty=_dec(d.get("origQty")),
            executed_qty=_dec(d.get("executedQty")),
            avg_price=_dec(d.get("avgPrice")),
            reduce_only=bool(d.get("reduceOnly", False)),
            close_position=bool(d.get("closePosition", False)),
            stop_price=_dec(d.get("stopPrice")),
            fee=_ZERO,  # 주문 조회에는 수수료가 없다 — 체결 내역(userTrades)에서 따로 얻는다
            fee_asset="USDT",
        )

    def _stop(self, d: Mapping[str, Any]) -> FuturesOrderResult:
        status_text = str(d.get("algoStatus", ""))
        executed, avg, status = _ZERO, _ZERO, OrderStatus.NEW
        if status_text in {"CANCELED", "EXPIRED"}:
            status = OrderStatus(status_text)
        elif status_text in _ALGO_TRIGGERED and d.get("actualOrderId"):
            actual = self._request(
                "/fapi/v1/order",
                {"symbol": d["symbol"], "orderId": d["actualOrderId"]},
                signed=True,
            )
            status = _STATUS.get(str(actual.get("status")), OrderStatus.NEW)
            executed, avg = _dec(actual.get("executedQty")), _dec(actual.get("avgPrice"))
        # TRIGGERED 인데 실제 주문이 아직 없으면 NEW 로 둔다 — 다음 복구가 다시 조회한다
        return FuturesOrderResult(
            client_order_id=str(d.get("clientAlgoId", "")),
            exchange_order_id=str(d.get("algoId", "")),
            symbol=str(d["symbol"]),
            side=str(d["side"]),
            order_type=FuturesOrderType.STOP_MARKET,
            status=status,
            orig_qty=_dec(d.get("quantity")),
            executed_qty=executed,
            avg_price=avg,
            reduce_only=bool(d.get("reduceOnly", True)),
            close_position=bool(d.get("closePosition", False)),
            stop_price=_dec(d.get("triggerPrice")),
            fee=_ZERO,
            fee_asset="USDT",
        )
