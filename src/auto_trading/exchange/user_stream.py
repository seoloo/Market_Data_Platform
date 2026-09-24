"""USD-M 사용자 스트림 이벤트 해석 (순수 함수, I/O 없음).

필드는 2026-09-24 테스트넷 실측 원문으로 확인했다 (docs/research/26 §6).
연결·재연결·listenKey 수명은 :mod:`auto_trading.exchange.binance_usdm_stream` 이 맡는다.

* ``ORDER_TRADE_UPDATE`` — 일반 주문의 생성·체결·취소. **발동한 보호 스톱의 실제 시장가 주문도
  여기로 온다**: ``c`` 가 스톱의 ``clientAlgoId`` 와 같고 ``st=ALGO_CONDITION``·``si=algoId``.
* ``ALGO_UPDATE`` — 보호 스톱(Algo) 상태: NEW → TRIGGERING → TRIGGERED(``ai`` 실제 주문 ID)
  → FINISHED(``ap``·``aq`` 체결가·수량). 그 밖에 CANCELED·REJECTED·EXPIRED.
* ``ACCOUNT_UPDATE`` — 잔고·포지션 변화 (``m`` 이유: ORDER·FUNDING_FEE 등).
* ``listenKeyExpired`` — listenKey 만료. 새 키로 다시 연결해야 한다.

**실측: 이벤트 순서가 뒤바뀐다.** 발동한 스톱의 체결(ORDER_TRADE_UPDATE FILLED)이 ALGO_UPDATE
TRIGGERING 보다 먼저 왔다. 그래서 이벤트를 상태로 직접 적용하지 않고, "이 주문을 다시 조회하라"는
신호로만 쓴다(:mod:`auto_trading.execution.stream_sync`). 거래소 조회가 진실의 원천이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = [
    "ALGO_TERMINAL",
    "AccountUpdate",
    "AlgoUpdate",
    "ListenKeyExpired",
    "OrderUpdate",
    "OtherEvent",
    "UserEvent",
    "parse_user_event",
]

ALGO_TERMINAL = frozenset({"CANCELED", "FINISHED", "REJECTED", "EXPIRED"})
_ZERO = Decimal(0)


def _dec(value: object) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else _ZERO


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    symbol: str
    client_order_id: str
    side: str
    order_id: str
    execution_type: str
    """``x``: NEW·TRADE·CANCELED·EXPIRED·CALCULATED(청산)·AMENDMENT 등."""
    status: str
    """``X``: NEW·PARTIALLY_FILLED·FILLED·CANCELED·EXPIRED 등."""
    last_qty: Decimal
    filled_qty: Decimal
    last_price: Decimal
    avg_price: Decimal
    fee: Decimal
    fee_asset: str
    realized_pnl: Decimal
    reduce_only: bool
    close_position: bool
    algo_id: str | None
    """발동한 Algo 스톱이 만든 주문이면 그 algoId (``st=ALGO_CONDITION``)."""
    event_ms: int
    trade_ms: int


@dataclass(frozen=True, slots=True)
class AlgoUpdate:
    symbol: str
    client_algo_id: str
    algo_id: str
    side: str
    status: str
    trigger_price: Decimal
    actual_order_id: str | None
    """TRIGGERED 부터 채워지는 실제 시장가 주문 ID."""
    filled_qty: Decimal
    avg_price: Decimal
    event_ms: int


@dataclass(frozen=True, slots=True)
class AccountUpdate:
    reason: str
    positions: dict[str, Decimal]
    """이번에 바뀐 포지션만 (부호 있는 수량)."""
    wallet: dict[str, Decimal]
    event_ms: int


@dataclass(frozen=True, slots=True)
class ListenKeyExpired:
    event_ms: int


@dataclass(frozen=True, slots=True)
class OtherEvent:
    event_type: str
    event_ms: int


UserEvent = OrderUpdate | AlgoUpdate | AccountUpdate | ListenKeyExpired | OtherEvent


def parse_user_event(data: Mapping[str, Any]) -> UserEvent:
    """원문 하나를 해석한다. 모르는 이벤트는 :class:`OtherEvent` — 버리지 않고 기록할 수 있게.

    필수 필드가 없으면 ``KeyError``/``ValueError`` 를 올린다(호출자가 원문과 함께 기록한다).
    """
    kind = str(data.get("e", ""))
    event_ms = int(data.get("E", 0))
    if kind == "ORDER_TRADE_UPDATE":
        o = data["o"]
        from_algo = str(o.get("st", "")) == "ALGO_CONDITION"
        return OrderUpdate(
            symbol=str(o["s"]),
            client_order_id=str(o["c"]),
            side=str(o["S"]),
            order_id=str(o.get("i", "")),
            execution_type=str(o.get("x", "")),
            status=str(o["X"]),
            last_qty=_dec(o.get("l")),
            filled_qty=_dec(o.get("z")),
            last_price=_dec(o.get("L")),
            avg_price=_dec(o.get("ap")),
            fee=_dec(o.get("n")),
            fee_asset=str(o.get("N") or "USDT"),
            realized_pnl=_dec(o.get("rp")),
            reduce_only=bool(o.get("R", False)),
            close_position=bool(o.get("cp", False)),
            algo_id=str(o["si"]) if from_algo and o.get("si") else None,
            event_ms=event_ms,
            trade_ms=int(o.get("T", data.get("T", 0))),
        )
    if kind == "ALGO_UPDATE":
        o = data["o"]
        return AlgoUpdate(
            symbol=str(o["s"]),
            client_algo_id=str(o["caid"]),
            algo_id=str(o.get("aid", "")),
            side=str(o["S"]),
            status=str(o["X"]),
            trigger_price=_dec(o.get("tp")),
            actual_order_id=str(o["ai"]) if o.get("ai") else None,
            filled_qty=_dec(o.get("aq")),
            avg_price=_dec(o.get("ap")),
            event_ms=event_ms,
        )
    if kind == "ACCOUNT_UPDATE":
        a = data["a"]
        positions = {
            str(p["s"]): _dec(p.get("pa"))
            for p in a.get("P", ())
            if str(p.get("ps", "BOTH")) == "BOTH"
        }
        wallet = {str(b["a"]): _dec(b.get("wb")) for b in a.get("B", ())}
        return AccountUpdate(str(a.get("m", "")), positions, wallet, event_ms)
    if kind == "listenKeyExpired":
        return ListenKeyExpired(event_ms)
    return OtherEvent(kind, event_ms)
