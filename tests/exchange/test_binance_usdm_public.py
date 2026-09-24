"""Binance USD-M 공개 시세 어댑터 — 네트워크 없이 MockTransport로 검증."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from auto_trading.core.errors import ExchangeError, RateLimitError
from auto_trading.exchange.binance_usdm_public import BinanceUsdmPublic

D = 86_400_000
DAY0 = 1_700_006_400_000  # 00:00 UTC


def adapter(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[BinanceUsdmPublic, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return BinanceUsdmPublic(client=client), seen


def kline(open_ms: int, close: str, *, closed: bool = True) -> list[object]:
    close_ms = open_ms + D - 1 if closed else open_ms + 3_600_000
    return [open_ms, close, close, "0.5", close, "10", close_ms, "100", 5, "0", "0", "0"]


class TestClosedDaily:
    def test_drops_the_candle_still_forming(self) -> None:
        rows = [kline(DAY0, "100.5"), kline(DAY0 + D, "101.25", closed=False)]
        api, _ = adapter(lambda r: httpx.Response(200, json=rows))
        out = api.closed_daily("BTCUSDT", DAY0 + D - 1)
        assert [c.open_ms for c in out] == [DAY0]
        assert out[0].close == Decimal("100.5")
        assert out[0].low == Decimal("0.5")

    def test_drops_candles_after_as_of(self) -> None:
        rows = [kline(DAY0, "1"), kline(DAY0 + D, "2")]
        api, _ = adapter(lambda r: httpx.Response(200, json=rows))
        assert [c.open_ms for c in api.closed_daily("BTCUSDT", DAY0 + D - 1)] == [DAY0]

    def test_uses_the_futures_klines_endpoint(self) -> None:
        api, seen = adapter(lambda r: httpx.Response(200, json=[]))
        api.closed_daily("ETHUSDT", DAY0 + D - 1, limit=5)
        url = urlparse(str(seen[0].url))
        assert url.netloc == "fapi.binance.com"
        assert url.path == "/fapi/v1/klines"
        query = parse_qs(url.query)
        assert query["interval"] == ["1d"]
        assert query["limit"] == ["5"]


class TestFunding:
    def test_parses_sorts_and_keeps_only_the_window(self) -> None:
        body = [
            {"symbol": "BTCUSDT", "fundingTime": DAY0 + 28_800_003, "fundingRate": "0.00010000"},
            {"symbol": "BTCUSDT", "fundingTime": DAY0 + 2, "fundingRate": "-0.00002500"},
            {"symbol": "BTCUSDT", "fundingTime": DAY0 + D + 1, "fundingRate": "0.5"},
        ]
        api, _ = adapter(lambda r: httpx.Response(200, json=body))
        got = api.funding("BTCUSDT", DAY0, DAY0 + D - 1)
        assert got == [(DAY0 + 2, -0.000025), (DAY0 + 28_800_003, 0.0001)]

    def test_a_truncated_page_is_refused(self) -> None:
        """1,000건에서 잘린 결과로 funding 을 계산하면 조용히 덜 낸다."""
        body = [
            {"symbol": "BTCUSDT", "fundingTime": DAY0 + k, "fundingRate": "0.0001"}
            for k in range(1000)
        ]
        api, _ = adapter(lambda r: httpx.Response(200, json=body))
        with pytest.raises(ExchangeError, match="잘렸다"):
            api.funding("BTCUSDT", DAY0, DAY0 + D - 1)


class TestErrorsAndSafety:
    def test_rate_limit_is_its_own_error(self) -> None:
        api, _ = adapter(lambda r: httpx.Response(429, json={"code": -1003, "msg": "too many"}))
        with pytest.raises(RateLimitError):
            api.last_price("BTCUSDT")

    def test_api_error_carries_the_code(self) -> None:
        api, _ = adapter(lambda r: httpx.Response(400, json={"code": -1121, "msg": "bad symbol"}))
        with pytest.raises(ExchangeError, match="-1121"):
            api.last_price("NOPE")

    def test_transport_failure_becomes_exchange_error(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        api, _ = adapter(boom)
        with pytest.raises(ExchangeError, match="요청 실패"):
            api.last_price("BTCUSDT")

    def test_never_sends_an_api_key(self) -> None:
        api, seen = adapter(lambda r: httpx.Response(200, json={"price": "1"}))
        api.last_price("BTCUSDT")
        assert "x-mbx-apikey" not in {k.lower() for k in seen[0].headers}
        assert "signature" not in str(seen[0].url)

    def test_has_no_order_or_account_methods(self) -> None:
        """주문·계좌 경로가 존재하지 않아야 한다 (Phase 8 승인 전)."""
        names = {n for n in dir(BinanceUsdmPublic) if not n.startswith("_")}
        assert names == {"base_url", "closed_daily", "funding", "last_price", "close"}
