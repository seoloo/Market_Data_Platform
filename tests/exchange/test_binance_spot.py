"""Binance 현물 공개 시세 어댑터 — 네트워크 없이 MockTransport로 검증. `.env` 읽기 포함.

예전의 서명 · 잔고 · 주문 조회 · 키 권한 시험은 그 코드를 걷어 낼 때(README 6-3) 함께 지웠다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from auto_trading.config.secrets import read_env_file
from auto_trading.core.errors import ExchangeError, RateLimitError
from auto_trading.exchange.binance_spot import BinanceSpotPublic

DAY_MS = 86_400_000


def adapter(routes: dict[str, httpx.Response], seen: list[httpx.Request]) -> BinanceSpotPublic:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return routes[request.url.path]

    return BinanceSpotPublic(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_filters_and_price_are_parsed_without_any_key() -> None:
    seen: list[httpx.Request] = []
    info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001000", "minQty": "0.00001000"},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                ],
            }
        ]
    }
    ex = adapter(
        {
            "/api/v3/exchangeInfo": httpx.Response(200, json=info),
            "/api/v3/ticker/price": httpx.Response(200, json={"price": "65000.01"}),
        },
        seen,
    )
    f = ex.filters("BTCUSDT")
    assert (f.step_size, f.min_qty, f.min_notional) == (
        Decimal("0.00001"),
        Decimal("0.00001"),
        Decimal(5),
    )
    assert ex.last_price("BTCUSDT") == Decimal("65000.01")
    assert all("X-MBX-APIKEY" not in r.headers and "signature" not in str(r.url) for r in seen)


@pytest.mark.leakage
def test_closed_daily_drops_the_unfinished_bar() -> None:
    """진행 중인 봉을 쓰면 미래 값이 섞인다 — 마감된 봉만."""

    def row(open_ms: int) -> list[object]:
        return [open_ms, "1", "2", "0.5", "1.5", "10", open_ms + DAY_MS - 1, "15", 7]

    as_of = 3 * DAY_MS - 1  # 이틀째 봉 마감 시각
    rows = [row(0), row(DAY_MS), row(2 * DAY_MS)]  # 셋째 봉은 아직 진행 중
    ex = adapter({"/api/v3/klines": httpx.Response(200, json=rows)}, [])
    bars = ex.closed_daily("BTCUSDT", as_of - DAY_MS)
    assert [b.open_ms for b in bars] == [0, DAY_MS]
    assert bars[0].close == Decimal("1.5") and bars[0].trade_count == 7


def test_order_book_and_errors_are_mapped() -> None:
    depth = {"bids": [["100.0", "1"]], "asks": [["100.5", "2"]]}
    ex = adapter({"/api/v3/depth": httpx.Response(200, json=depth)}, [])
    assert ex.order_book("BTCUSDT") == (
        [(Decimal("100.0"), Decimal(1))],
        [(Decimal("100.5"), Decimal(2))],
    )
    limited = adapter({"/api/v3/ticker/price": httpx.Response(429, json={})}, [])
    with pytest.raises(RateLimitError):
        limited.last_price("BTCUSDT")
    bad = httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
    with pytest.raises(ExchangeError, match="-1121"):
        adapter({"/api/v3/ticker/price": bad}, []).last_price("NOPE")


def test_public_adapter_has_no_account_or_order_methods() -> None:
    ex = BinanceSpotPublic()
    for name in ("balances", "api_restrictions", "get_order", "place_market_order"):
        assert not hasattr(ex, name)
    ex.close()


def test_env_file_parsing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        '# 주석\nAT_MODE=paper   # 인라인 주석\nQUOTED="v"\nEMPTY=\n',
        encoding="utf-8",
    )
    values = read_env_file(env)
    assert values["AT_MODE"] == "paper" and values["QUOTED"] == "v" and values["EMPTY"] == ""
