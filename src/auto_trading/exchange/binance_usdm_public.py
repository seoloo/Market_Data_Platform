"""Binance USD-M 무기한 **공개 시세** 어댑터 (O/v1 Paper, docs/research/25).

키를 받지 않는다. 일봉·funding 이력·현재가만 조회하고, 계좌·주문 경로는 이 모듈에 없다.
선물 주문 인프라(멱등 주문·보호주문·reconcile)는 실거래 전 Phase 5에서 따로 만든다.
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

__all__ = ["BASE_URL", "BinanceUsdmPublic"]

BASE_URL = "https://fapi.binance.com"
_FUNDING_LIMIT = 1000


class BinanceUsdmPublic:
    __slots__ = ("_client", "base_url")

    def __init__(self, *, client: httpx.Client | None = None, base_url: str = BASE_URL) -> None:
        self._client = client if client is not None else httpx.Client(timeout=10.0)
        self.base_url = base_url.rstrip("/")

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        url = f"{self.base_url}{path}?{urlencode(dict(params))}"
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise ExchangeError(f"Binance 선물 요청 실패: {path} ({type(exc).__name__})") from exc
        if response.status_code in (418, 429):
            raise RateLimitError(f"Binance 선물 레이트 리밋 {response.status_code}: {path}")
        body = response.json() if response.content else {}
        if response.is_success:
            return body
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("msg") if isinstance(body, dict) else ""
        raise ExchangeError(f"Binance 선물 오류 {response.status_code} code={code}: {msg} ({path})")

    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]:
        """``as_of_ms``까지 **마감된** 무기한 일봉. 진행 중인 봉은 버린다."""
        rows = self._get(
            "/fapi/v1/klines",
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

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        """``[start_ms, end_ms]``에 정산된 funding ``(시각, 비율)``, 시각 순.

        한 번에 1,000건까지 준다. 그보다 많으면 잘린 결과로 계산하지 않고 거부한다.
        """
        rows = self._get(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": _FUNDING_LIMIT},
        )
        if len(rows) >= _FUNDING_LIMIT:
            raise ExchangeError(f"funding 조회가 {_FUNDING_LIMIT}건에서 잘렸다: {symbol}")
        out = [
            (int(r["fundingTime"]), float(r["fundingRate"]))
            for r in rows
            if start_ms <= int(r["fundingTime"]) <= end_ms
        ]
        return sorted(out)

    def last_price(self, symbol: str) -> Decimal:
        return Decimal(self._get("/fapi/v1/ticker/price", {"symbol": symbol})["price"])

    def close(self) -> None:
        self._client.close()
