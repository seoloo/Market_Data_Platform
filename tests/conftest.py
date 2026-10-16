"""모든 시험 공통 안전장치 — 시험이 진짜 거래소 · 진짜 돈에 닿지 못하게.

2026-10-16 사고: '전부 정리' 시험이 바이낸스 클라이언트를 가짜로 바꾸지 않아, 전체 시험을 돌린
09:58 에 실계좌 Vega 의 BTC · ETH · SOL 이 지정가로 팔렸다(``.env`` 의 주문 허용이 켜져 있었음).
그래서 두 겹으로 막는다.

1. 주문 허용 환경 변수를 전부 ``false`` 로 덮는다 — 로더는 ``.env`` 위에 환경 변수를 얹으므로
   ``.env`` 가 ``true`` 여도 시험 안에서는 꺼진다.
2. 진짜 네트워크 전송(``httpx.HTTPTransport``)을 막는다 — 가짜 전송(``MockTransport``)은 된다.
   시험이 실수로 진짜 클라이언트를 만들어도 요청이 나가지 않고 바로 실패한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

TRADING_FLAGS = (
    "BINANCE_LIVE_TRADING_ENABLED",
    "BINANCE_FUTURES_LIVE_TRADING_ENABLED",
    "TOSS_TRADING_ENABLED",
    "KIS_TRADING_ENABLED",
    "UPBIT_TRADING_ENABLED",
    "NAMU_TRADING_ENABLED",
)


class RealNetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_real_money(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for flag in TRADING_FLAGS:
        monkeypatch.setenv(flag, "false")

    def blocked(self: Any, request: httpx.Request) -> httpx.Response:
        raise RealNetworkBlockedError(f"시험에서 진짜 네트워크 금지: {request.url.host}")

    async def blocked_async(self: Any, request: httpx.Request) -> httpx.Response:
        raise RealNetworkBlockedError(f"시험에서 진짜 네트워크 금지: {request.url.host}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
    yield
