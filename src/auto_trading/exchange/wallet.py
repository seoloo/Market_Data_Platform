"""현물 ↔ USD-M 선물 지갑 이체 계약 (docs/research/53, O/v2 안전성 검증 V3).

Binance: ``POST /sapi/v1/asset/transfer``
(``type`` = ``MAIN_UMFUTURE`` 현물→선물 · ``UMFUTURE_MAIN`` 선물→현물),
이력 ``GET /sapi/v1/asset/transfer`` (최근 6개월, 기본 7일, 한 쪽 최대 100건).
API 키에 "Permits Universal Transfer" 권한이 필요하다.
**클라이언트가 정하는 멱등 ID 가 없다** — 응답의 ``tranId`` 만 있다.
그래서 응답을 잃으면 이력 조회로만 해소한다(:mod:`auto_trading.execution.wallet_transfers`).

이 모듈은 계약(Protocol)과 값 타입만 둔다.
실계좌 이체 구현은 Phase 8 2단계 승인 전까지 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

__all__ = ["TransferDirection", "TransferRow", "WalletTransfers"]


class TransferDirection(StrEnum):
    SPOT_TO_FUTURES = "MAIN_UMFUTURE"
    FUTURES_TO_SPOT = "UMFUTURE_MAIN"


@dataclass(frozen=True, slots=True)
class TransferRow:
    tran_id: str
    direction: TransferDirection
    asset: str
    amount: Decimal
    status: str
    """거래소 표기 (Binance: CONFIRMED 등)."""
    timestamp_ms: int


class WalletTransfers(Protocol):
    def transfer(self, direction: TransferDirection, asset: str, amount: Decimal) -> str:
        """이체하고 tranId 를 돌려준다.

        거부가 확실하면 OrderRejectedError, 결과를 모르면 ExchangeError(OrderStateUnknownError).
        """
        ...

    def transfers(
        self, direction: TransferDirection, start_ms: int, end_ms: int
    ) -> list[TransferRow]:
        """``[start_ms, end_ms]`` 이체 이력."""
        ...
