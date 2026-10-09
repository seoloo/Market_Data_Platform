from __future__ import annotations

from decimal import Decimal

import httpx
from scripts.run_paper_t2_upbit import UpbitKrwMarket


def _client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/candles/days"):
            assert request.url.params["market"] == "KRW-BTC"
            rows = [
                {
                    "candle_date_time_utc": f"2026-10-0{d}T00:00:00",
                    "opening_price": float(d),
                    "high_price": float(d) + 1,
                    "low_price": float(d) - 1,
                    "trade_price": float(d),
                    "candle_acc_trade_volume": 1.0,
                    "candle_acc_trade_price": 1.0,
                }
                for d in (9, 8, 7)
            ]
            return httpx.Response(200, json=rows)
        return httpx.Response(200, json=[{"trade_price": 123456789.0}])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_closed_daily_drops_open_bar_and_sorts() -> None:
    m = UpbitKrwMarket(_client())
    as_of = 1791503999999  # 2026-10-08 23:59:59.999 UTC
    candles = m.closed_daily("BTCUSDT", as_of)
    assert [c.close for c in candles] == [Decimal(7), Decimal(8)]
    assert candles[-1].close_ms == as_of
    assert m.last_price("BTCUSDT") == Decimal("123456789.0")
    assert m.filters("ETHUSDT").min_notional == Decimal(5000)
