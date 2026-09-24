"""USD-M 선물 주문 실행 — 멱등 주문·선기록·재시작 복구 (Phase 5 선물).

현물(:mod:`auto_trading.execution.spot_orders`)과 같은 규약 위에 선물 규칙을 더한다
(AGENTS §2, TRADING_CORE).

* **멱등:** client_order_id = 전략·결정 시각·종목·목적·방향 (+ 명시적 재발행 번호).
  재시작 세션이나 현재 시각을 쓰지 않는다. 같은 의도는 몇 번을 호출해도 주문 1건.
* **선기록:** 거래소 호출 전에 PENDING 을 커밋한다.
* **UNKNOWN 에서 추정 재주문 금지:** 결과를 모르면 거래소 조회로만 해소한다.
  거래소에 없다고 확인되면 NOT_PLACED 로 닫고 다시 보내지 않는다.
  다시 내려면 ``attempt`` 를 올려 **새 ID** 로 명시한다.
* **신규 진입만 막는다:** 결과를 모르는 주문이 하나라도 있으면 ``OPEN`` 은 거부한다. 그러나
  ``REDUCE``·``CLOSE``·``PROTECT`` 는 막지 않는다 — 위험 축소와 보호는 어떤 상태에서도 허용된다.
* **걸린 보호 스톱은 정상이다:** SUBMITTED 스톱은 막지 않고, 복구 때 조회해 발동·만료를 반영한다.
* **스톱은 다른 경로로 조회·취소한다:** Binance 조건부 주문은 Algo API 라서
  (:mod:`auto_trading.exchange.futures` 참조) 기록의 주문 종류를 보고 ``*_stop`` 을 부른다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal

from auto_trading.core.errors import ExchangeError, OrderRejectedError, ReconciliationError
from auto_trading.exchange.futures import FuturesExchange, FuturesOrderResult, OrderStatus
from auto_trading.storage.repositories.futures_orders import (
    TERMINAL_STATES,
    FuturesOrderRecord,
    FuturesOrderRepository,
)

__all__ = ["FuturesOrderManager", "futures_client_order_id"]

_MAX_ID_LEN = 36
_ID_PATTERN = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")
"""Binance USD-M newClientOrderId 허용 문자."""

_PURPOSE_CODE = {"OPEN": "O", "REDUCE": "R", "CLOSE": "C", "PROTECT": "P"}

_STATE_BY_STATUS = {
    OrderStatus.NEW: "SUBMITTED",
    OrderStatus.PARTIALLY_FILLED: "PARTIALLY_FILLED",
    OrderStatus.FILLED: "FILLED",
    OrderStatus.CANCELED: "CANCELED",
    OrderStatus.REJECTED: "REJECTED",
    OrderStatus.EXPIRED: "EXPIRED",
}


def futures_client_order_id(
    strategy_tag: str, decision_ms: int, symbol: str, purpose: str, side: str, attempt: int = 0
) -> str:
    """결정론적 주문 ID.

    ``attempt``는 실패가 확인된 뒤 사람이나 정책이 명시적으로 올리는 재발행 번호다.
    """
    if purpose not in _PURPOSE_CODE:
        raise ValueError(f"알 수 없는 목적: {purpose}")
    if attempt < 0:
        raise ValueError(f"attempt 는 0 이상이어야 한다: {attempt}")
    cid = f"{strategy_tag}-{decision_ms}-{symbol}-{_PURPOSE_CODE[purpose]}{side[0]}"
    if attempt:
        cid += f"-{attempt}"
    if len(cid) > _MAX_ID_LEN or not _ID_PATTERN.match(cid):
        raise ValueError(f"Binance client_order_id 규칙 위반(36자·허용 문자): {cid}")
    return cid


class FuturesOrderManager:
    __slots__ = ("_clock", "_exchange", "_mode", "_store", "_strategy_id", "_tag")

    def __init__(
        self,
        exchange: FuturesExchange,
        store: FuturesOrderRepository,
        *,
        mode: str,
        strategy_id: str,
        strategy_tag: str,
        clock: Callable[[], int],
    ) -> None:
        self._exchange = exchange
        self._store = store
        self._mode = mode
        self._strategy_id = strategy_id
        self._tag = strategy_tag
        self._clock = clock

    # ------------------------------------------------------------ 상태

    @property
    def tag(self) -> str:
        """client_order_id 앞머리 (재발행 번호를 찾는 쪽이 같은 규칙을 쓰게 한다)."""
        return self._tag

    def blocked(self) -> bool:
        """결과를 모르는 주문이 있으면 True — 신규 진입(OPEN) 금지."""
        return any(r.needs_reconcile for r in self._store.open())

    def working_stops(self, symbol: str | None = None) -> list[FuturesOrderRecord]:
        return [
            r
            for r in self._store.open()
            if r.is_working_stop and (symbol is None or r.symbol == symbol)
        ]

    # ------------------------------------------------------------ 주문

    def submit_market(
        self,
        *,
        decision_ms: int,
        symbol: str,
        side: str,
        quantity: Decimal,
        purpose: str,
        attempt: int = 0,
    ) -> FuturesOrderRecord:
        """시장가 의도 하나를 멱등하게 실행한다. OPEN 외에는 reduce-only 로 보낸다."""
        if purpose not in {"OPEN", "REDUCE", "CLOSE"}:
            raise ValueError(f"시장가 목적은 OPEN·REDUCE·CLOSE 중 하나다: {purpose}")
        cid = futures_client_order_id(self._tag, decision_ms, symbol, purpose, side, attempt)
        existing = self._store.get(cid)
        if existing is not None:
            return existing if existing.state in TERMINAL_STATES else self._reconcile(existing)
        if purpose == "OPEN" and self.blocked():
            raise ReconciliationError("결과를 모르는 주문이 있어 신규 진입 금지 — 먼저 recover()")
        record = self._new_record(
            cid,
            decision_ms,
            symbol,
            side,
            order_type="MARKET",
            purpose=purpose,
            reduce_only=purpose != "OPEN",
            close_position=False,
            qty=quantity,
            stop_price=Decimal(0),
        )
        return self._send(
            record,
            lambda: self._exchange.place_market_order(
                symbol, side, quantity, cid, reduce_only=record.reduce_only
            ),
        )

    def place_protective_stop(
        self,
        *,
        decision_ms: int,
        symbol: str,
        side: str,
        stop_price: Decimal,
        attempt: int = 0,
    ) -> FuturesOrderRecord:
        """closePosition 보호 스톱을 멱등하게 건다. 미해소 주문이 있어도 막지 않는다."""
        cid = futures_client_order_id(self._tag, decision_ms, symbol, "PROTECT", side, attempt)
        existing = self._store.get(cid)
        if existing is not None:
            if existing.state in TERMINAL_STATES or existing.is_working_stop:
                return existing
            return self._reconcile(existing)
        record = self._new_record(
            cid,
            decision_ms,
            symbol,
            side,
            order_type="STOP_MARKET",
            purpose="PROTECT",
            reduce_only=True,
            close_position=True,
            qty=Decimal(0),
            stop_price=stop_price,
        )
        return self._send(
            record, lambda: self._exchange.place_stop_market(symbol, side, stop_price, cid)
        )

    def cancel(self, client_order_id: str) -> FuturesOrderRecord:
        """걸린 주문을 취소한다. 이미 끝났으면 거래소 상태를 읽어 반영한다."""
        record = self._store.get(client_order_id)
        if record is None:
            raise ValueError(f"기록에 없는 주문: {client_order_id}")
        if record.state in TERMINAL_STATES:
            return record
        cancel = (
            self._exchange.cancel_stop
            if record.order_type == "STOP_MARKET"
            else self._exchange.cancel_order
        )
        try:
            result = cancel(record.symbol, client_order_id)
        except OrderRejectedError:
            return self._reconcile(record)  # 이미 발동·만료 — 최종 상태를 읽는다
        except ExchangeError as exc:
            return self._keep(record, f"취소 실패: {exc}")
        if result is None:
            return self._reconcile(record)
        return self._apply(record, result)

    def recover(self) -> list[FuturesOrderRecord]:
        """기동 시·주기적으로 열린 주문을 거래소 조회로 맞춘다. 재주문하지 않는다."""
        return [self._reconcile(r) for r in self._store.open()]

    # ------------------------------------------------------------ 내부

    def _new_record(
        self,
        cid: str,
        decision_ms: int,
        symbol: str,
        side: str,
        *,
        order_type: str,
        purpose: str,
        reduce_only: bool,
        close_position: bool,
        qty: Decimal,
        stop_price: Decimal,
    ) -> FuturesOrderRecord:
        now = self._clock()
        record = FuturesOrderRecord(
            client_order_id=cid,
            mode=self._mode,
            strategy_id=self._strategy_id,
            decision_ms=decision_ms,
            symbol=symbol,
            side=side,
            order_type=order_type,
            purpose=purpose,
            reduce_only=reduce_only,
            close_position=close_position,
            qty=qty,
            stop_price=stop_price,
            state="PENDING",
            created_ms=now,
            updated_ms=now,
        )
        self._store.insert(record)  # 선기록 — 거래소 호출 전
        return record

    def _send(
        self, record: FuturesOrderRecord, call: Callable[[], FuturesOrderResult]
    ) -> FuturesOrderRecord:
        try:
            result = call()
        except OrderRejectedError as exc:
            rejected = record.with_state("REJECTED", self._clock(), error=str(exc))
            self._store.update(rejected)
            return rejected
        except ExchangeError as exc:
            unknown = record.with_state("UNKNOWN", self._clock(), error=str(exc))
            self._store.update(unknown)
            return unknown
        return self._apply(record, result)

    def _reconcile(self, record: FuturesOrderRecord) -> FuturesOrderRecord:
        lookup = (
            self._exchange.get_stop
            if record.order_type == "STOP_MARKET"
            else self._exchange.get_order
        )
        try:
            result = lookup(record.symbol, record.client_order_id)
        except ExchangeError as exc:
            return self._keep(record, str(exc))
        if result is not None:
            return self._apply(record, result)
        if record.state in {"PENDING", "UNKNOWN"}:
            closed = record.with_state(
                "NOT_PLACED", self._clock(), error="거래소에 없음 (조회로 확인)"
            )
            self._store.update(closed)
            return closed
        return self._keep(record, "접수 기록이 있는데 거래소에서 찾지 못함")

    def _keep(self, record: FuturesOrderRecord, error: str) -> FuturesOrderRecord:
        kept = record.with_state(record.state, self._clock(), error=error)
        self._store.update(kept)
        return kept

    def _apply(self, record: FuturesOrderRecord, result: FuturesOrderResult) -> FuturesOrderRecord:
        updated = record.with_state(
            _STATE_BY_STATUS[result.status],
            self._clock(),
            exchange_order_id=result.exchange_order_id,
            filled_qty=result.executed_qty,
            avg_price=result.avg_price,
            fee=result.fee,
            fee_asset=result.fee_asset,
            error=None,
        )
        self._store.update(updated)
        return updated
