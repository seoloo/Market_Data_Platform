"""업비트 주문 스위치 (2026-10-16 점검) — 꺼져 있으면 요청 자체를 보내지 않는다."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from auto_trading.core.errors import ExchangeError
from auto_trading.exchange.upbit import UpbitClient, upbit_trading_enabled


def _client(enabled: bool, seen: list[httpx.Request]) -> UpbitClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "uuid": "u1",
                "identifier": "liq-1",
                "state": "wait",
                "side": "ask",
                "volume": "0.01",
                "executed_volume": "0",
                "price": "100",
                "paid_fee": "0",
                "trades": [],
            },
        )

    return UpbitClient(
        "ak",
        "sk",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
        trading_enabled=enabled,
    )


def test_switch_off_sends_nothing() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(ExchangeError, match="UPBIT_TRADING_ENABLED"):
        _client(False, seen).place_limit_ioc("KRW-BTC", "ask", Decimal("0.01"), Decimal(100), "x")
    assert seen == []


def test_switch_on_sends_limit_ioc() -> None:
    seen: list[httpx.Request] = []
    _client(True, seen).place_limit_ioc("KRW-BTC", "ask", Decimal("0.01"), Decimal(100), "x")
    assert len(seen) == 1 and b'"ord_type":"limit"' in seen[0].content.replace(b" ", b"")


def test_flag_reads_env_over_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPBIT_TRADING_ENABLED", raising=False)
    assert upbit_trading_enabled({"UPBIT_TRADING_ENABLED": "true"})
    assert not upbit_trading_enabled({})
    monkeypatch.setenv("UPBIT_TRADING_ENABLED", "false")  # 시험 · 비상시 환경 변수가 이긴다
    assert not upbit_trading_enabled({"UPBIT_TRADING_ENABLED": "true"})
