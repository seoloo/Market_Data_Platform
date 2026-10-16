"""Binance USDⓈ-M **실계좌** 주문 어댑터 — Altair 실거래 전용 (2026-10-16 사용자 "실행기 작업해줘").

데모 어댑터(:class:`BinanceUsdmTestnet`)와 같은 주문 · 취소 · 조회 코드를 쓰고, 다른 점만 여기 있다.

* 주소는 실계좌(``https://fapi.binance.com``)만 받는다.
* 키: ``BINANCE_FUTURES_LIVE_API_KEY`` · ``BINANCE_FUTURES_LIVE_API_SECRET`` (선물 거래 · 읽기만,
  출금 금지, 집 IP 제한 — 사용자가 만든다). 없으면 Vega 의 ``BINANCE_LIVE_API_*`` 를 쓴다
  (같은 계좌, 그 키에 선물 권한이 있어야 한다).
  ``BINANCE_FUTURES_LIVE_TRADING_ENABLED=true`` 일 때만 주문 · 취소 · 설정을 보낸다.
* 사용자 규칙(지정가만): 일반 주문은 지정가 IOC · post-only 지정가만. **시장가 주문과 목표가
  시장가 청산은 막는다.** 비상 STOP_MARKET(Algo, 포지션 전체 · 수량 지정 reduce-only)은 예외.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import BinanceCredentials, read_env_file
from auto_trading.core.errors import ConfigError
from auto_trading.exchange.binance_usdm import BASE_URL, BinanceUsdmReadOnly
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet
from auto_trading.exchange.futures import FuturesOrderResult
from auto_trading.exchange.overseas import TradingDisabledError

__all__ = ["LIVE_URL", "BinanceUsdmLive", "load_live_futures"]

LIVE_URL = BASE_URL


def load_live_futures(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> tuple[BinanceCredentials, bool]:
    """(선물 거래 키, 주문 허용). 키가 없으면 ConfigError — 값은 메시지에 넣지 않는다."""
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    key = merged.get("BINANCE_FUTURES_LIVE_API_KEY", "").strip()
    secret = merged.get("BINANCE_FUTURES_LIVE_API_SECRET", "").strip()
    if not key or not secret:
        key = merged.get("BINANCE_LIVE_API_KEY", "").strip()
        secret = merged.get("BINANCE_LIVE_API_SECRET", "").strip()
    if not key or not secret:
        raise ConfigError(
            "BINANCE_FUTURES_LIVE_API_KEY / _SECRET (또는 BINANCE_LIVE_API_*) 가 .env 에 없다"
            " (거래 키는 사용자가 만든다)"
        )
    enabled = merged.get("BINANCE_FUTURES_LIVE_TRADING_ENABLED", "").strip().lower() == "true"
    return BinanceCredentials(key, secret), enabled


class BinanceUsdmLive(BinanceUsdmTestnet):
    """``trading_enabled=False`` 면 조회만 — 주문 · 취소 · 설정은 막힌다(미리 보기 · 상태용)."""

    __slots__ = ("trading_enabled",)

    def __init__(
        self,
        credentials: BinanceCredentials,
        *,
        trading_enabled: bool,
        client: httpx.Client | None = None,
        base_url: str = LIVE_URL,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if base_url.rstrip("/") != LIVE_URL:
            raise ConfigError(f"실계좌 어댑터에 실계좌가 아닌 주소: {base_url}")
        # 데모 어댑터의 '테스트넷 주소만' 검사를 건너뛰고 읽기 어댑터로 바로 만든다.
        BinanceUsdmReadOnly.__init__(
            self, credentials, client=client, base_url=base_url, clock=clock
        )
        self.trading_enabled = trading_enabled

    def _guard(self) -> None:
        if not self.trading_enabled:
            raise TradingDisabledError(
                "BINANCE_FUTURES_LIVE_TRADING_ENABLED 가 true 가 아니다 — 선물 실주문 안 함"
            )

    def _send(self, method: str, path: str, params: Mapping[str, Any]) -> Any:
        self._guard()
        return super()._send(method, path, params)

    def _cancel(self, path: str, params: Mapping[str, Any]) -> Any:
        self._guard()
        return super()._cancel(path, params)

    def set_leverage(self, symbol: str, leverage: int) -> int:
        self._guard()
        return super().set_leverage(symbol, leverage)

    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        self._guard()
        super().set_margin_type(symbol, margin_type)

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str, *, reduce_only: bool
    ) -> FuturesOrderResult:
        raise TradingDisabledError("실계좌 시장가 주문 금지 (사용자 규칙: 지정가만)")

    def place_take_profit_market(
        self, symbol: str, side: str, trigger_price: Decimal, client_order_id: str
    ) -> FuturesOrderResult:
        raise TradingDisabledError("실계좌 목표가 시장가 청산 금지 (사용자 규칙: 지정가만)")

    def transfers(self, asset: str, start_ms: int) -> list[tuple[int, Decimal, str]]:
        """선물 지갑으로 들어오고 나간 이체 (시각, 금액 + 들어옴 / − 나감, 거래 번호).

        ``/fapi/v1/income`` ``incomeType=TRANSFER`` — 입금 · 현물 ↔ 선물 이체를 손익과 가른다."""
        rows = self._request(
            "/fapi/v1/income",
            {"incomeType": "TRANSFER", "startTime": start_ms, "limit": 1000},
            signed=True,
        )
        out: list[tuple[int, Decimal, str]] = []
        for r in rows or []:
            if str(r.get("asset")) != asset:
                continue
            out.append((int(r["time"]), Decimal(str(r["income"])), str(r.get("tranId", ""))))
        return sorted(out)

    def margin_balance(self) -> Decimal:
        """선물 지갑 전체의 증거금 잔고(달러, 미실현 손익 포함) — 다중 자산 모드 장부.

        ``/fapi/v2/account`` ``totalMarginBalance``. 다중 자산 모드에선 USDT · USDC 를 함께 센다."""
        data: Any = self._request("/fapi/v2/account", signed=True)
        return Decimal(str((data or {}).get("totalMarginBalance") or "0"))

    def set_multi_assets_margin(self, on: bool) -> None:
        """다중 자산 모드 켜기 · 끄기 (사용자 2026-10-16 '다중 자산 모드로 해주면 돼').

        포지션 · 걸린 주문이 있으면 거래소가 거절한다. 주문 허용이 꺼져 있으면 막는다."""
        self._guard()
        self._request(
            "/fapi/v1/multiAssetsMargin",
            {"multiAssetsMargin": "true" if on else "false"},
            signed=True,
            method="POST",
        )

    def multi_assets_margin(self) -> bool:
        """다중 자산 모드면 True — USDT 도 USDC 계약 증거금이 된다(장부 계산이 달라진다)."""
        data: Any = self._request("/fapi/v1/multiAssetsMargin", signed=True)
        return bool((data or {}).get("multiAssetsMargin"))
