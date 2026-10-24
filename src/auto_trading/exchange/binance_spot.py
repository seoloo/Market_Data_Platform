"""Binance 현물 **공개 시세** 어댑터 — 키 없이 일봉 · 가격 · 주문 규칙 · 호가만 읽는다.

예전에는 키로 서명해 잔고 · 주문 조회 · 키 권한을 읽는 부분이 함께 있었다(T/v2 Phase 5-4).
실주문 · 실계좌 코드를 걷어 낼 때(README 6-3) 그 부분은 뺐다 — 이 클래스는 서명 요청을 만들 수
없고, 주문 경로도 없다.
Paper · 그림자 운영 · 연구 스크립트가 공개 시세용으로 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import ExchangeError, RateLimitError
from auto_trading.core.types import Candle
from auto_trading.exchange.spot import SpotFilters

__all__ = ["BASE_URL", "BinanceSpotPublic"]

BASE_URL = "https://api.binance.com"


class BinanceSpotPublic:
    __slots__ = ("_client", "base_url")

    def __init__(self, *, client: httpx.Client | None = None, base_url: str = BASE_URL) -> None:
        self._client = client if client is not None else httpx.Client(timeout=10.0)
        self.base_url = base_url.rstrip("/")

    def _request(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        query_string = urlencode(dict(params or {}))
        url = f"{self.base_url}{path}" + (f"?{query_string}" if query_string else "")
        try:
            response = self._client.get(url)
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
        """호가 (매수 호가, 매도 호가) — 각 (가격, 수량), 좋은 가격부터."""
        data = self._request("/api/v3/depth", {"symbol": symbol, "limit": limit})
        bids = [(Decimal(p), Decimal(q)) for p, q in data["bids"]]
        asks = [(Decimal(p), Decimal(q)) for p, q in data["asks"]]
        return bids, asks

    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]:
        """``as_of_ms`` 까지 **마감된** 일봉. 진행 중인 봉은 버린다."""
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

    def close(self) -> None:
        self._client.close()
