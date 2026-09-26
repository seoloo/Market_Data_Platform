"""해외주식·ETF 브로커 계약 (한국투자증권 등). 구현: :mod:`auto_trading.exchange.kis`.

암호화폐 거래소 계약(:mod:`spot`·:mod:`futures`)과 따로 둔다 — 장 시간·통화·종목 코드가 다르다.
일봉은 기존 신호 계약(``closed_daily(symbol, as_of_ms, limit) -> list[Candle]``)과 같은 모양이라
T/v2 규칙·연구 코드가 그대로 쓸 수 있다.

주문은 이 계약에 **없다.** 실전 주문은 별도 승인 뒤 따로 더한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from auto_trading.core.errors import ExchangeError
from auto_trading.core.types import Candle

__all__ = [
    "OverseasBalance",
    "OverseasBroker",
    "OverseasPosition",
    "OverseasQuote",
    "TradingDisabledError",
]


class TradingDisabledError(ExchangeError):
    """주문이 막혀 있다(설정·승인 전). 재시도해도 같다."""


@dataclass(frozen=True, slots=True)
class OverseasQuote:
    symbol: str
    exchange: str
    """거래소 코드 (KIS: NAS·NYS·AMS)."""
    last: Decimal
    prev_close: Decimal | None
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class OverseasPosition:
    symbol: str
    exchange: str
    qty: Decimal
    avg_price: Decimal
    last: Decimal
    value: Decimal
    """평가 금액 (현지 통화)."""
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class OverseasBalance:
    positions: tuple[OverseasPosition, ...]
    value: Decimal
    """보유 종목 평가 합계 (현지 통화)."""
    currency: str = "USD"


class OverseasBroker(Protocol):
    def quote(self, symbol: str) -> OverseasQuote: ...

    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]: ...

    def overseas_balance(self) -> OverseasBalance: ...
