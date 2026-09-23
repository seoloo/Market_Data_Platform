"""현물 주문 실행 — 멱등 주문·선기록·재시작 복구 (T/v2 Phase 5).

규약 (AGENTS §2):

* **멱등:** client_order_id = 전략·결정 시각·종목·방향. 같은 의도는 몇 번을 호출해도 주문 1건.
* **선기록:** 거래소 호출 전에 PENDING을 커밋한다. 프로세스가 어디서 죽어도 기록이 남는다.
* **UNKNOWN에서 추정 재주문 금지:** 결과를 모르면 거래소 조회로만 해소한다. 거래소에 없다고 확인되면
  NOT_PLACED로 닫고 **다시 보내지 않는다** — 다음 결정(다음 날)이 새 ID로 판단한다.
* **fail-closed:** 해소되지 않은 주문이 있으면 새 주문을 내지 않는다
  (:meth:`SpotOrderManager.blocked`).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from auto_trading.core.errors import ExchangeError, OrderRejectedError
from auto_trading.exchange.spot import OrderStatus, SpotExchange, SpotOrderResult
from auto_trading.storage.repositories.spot_orders import (
    TERMINAL_STATES,
    SpotOrderRecord,
    SpotOrderRepository,
)

__all__ = ["SpotOrderManager", "client_order_id"]

_MAX_ID_LEN = 36
"""Binance newClientOrderId 최대 길이."""

_STATE_BY_STATUS = {
    OrderStatus.NEW: "SUBMITTED",
    OrderStatus.PARTIALLY_FILLED: "PARTIALLY_FILLED",
    OrderStatus.FILLED: "FILLED",
    OrderStatus.CANCELED: "CANCELED",
    OrderStatus.REJECTED: "REJECTED",
    OrderStatus.EXPIRED: "EXPIRED",
}


def client_order_id(strategy_tag: str, decision_ms: int, symbol: str, side: str) -> str:
    """결정론적 주문 ID. 재시작 후에도 같은 의도는 같은 ID다."""
    cid = f"{strategy_tag}-{decision_ms}-{symbol}-{side[0]}"
    if len(cid) > _MAX_ID_LEN:
        raise ValueError(f"client_order_id가 {_MAX_ID_LEN}자를 넘는다: {cid}")
    return cid


class SpotOrderManager:
    __slots__ = ("_clock", "_exchange", "_mode", "_store", "_strategy_id", "_tag")

    def __init__(
        self,
        exchange: SpotExchange,
        store: SpotOrderRepository,
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

    def blocked(self) -> bool:
        """해소되지 않은 주문이 있으면 True — 신규 주문 금지."""
        return bool(self._store.unresolved())

    def submit(
        self,
        *,
        decision_ms: int,
        symbol: str,
        side: str,
        quantity: Decimal,
        target_weight: Decimal,
    ) -> SpotOrderRecord:
        """의도 하나를 멱등하게 실행한다. 이미 기록이 있으면 거래소에 다시 보내지 않는다."""
        cid = client_order_id(self._tag, decision_ms, symbol, side)
        existing = self._store.get(cid)
        if existing is not None:
            return existing if existing.state in TERMINAL_STATES else self._reconcile(existing)
        now = self._clock()
        record = SpotOrderRecord(
            client_order_id=cid,
            mode=self._mode,
            strategy_id=self._strategy_id,
            decision_ms=decision_ms,
            symbol=symbol,
            side=side,
            qty=quantity,
            target_weight=target_weight,
            state="PENDING",
            created_ms=now,
            updated_ms=now,
        )
        self._store.insert(record)
        try:
            result = self._exchange.place_market_order(symbol, side, quantity, cid)
        except OrderRejectedError as exc:
            rejected = record.with_state("REJECTED", self._clock(), error=str(exc))
            self._store.update(rejected)
            return rejected
        except ExchangeError as exc:
            unknown = record.with_state("UNKNOWN", self._clock(), error=str(exc))
            self._store.update(unknown)
            return unknown
        return self._apply(record, result)

    def recover(self) -> list[SpotOrderRecord]:
        """기동 시 해소되지 않은 주문을 거래소 조회로 맞춘다. 재주문하지 않는다."""
        return [self._reconcile(r) for r in self._store.unresolved()]

    def _reconcile(self, record: SpotOrderRecord) -> SpotOrderRecord:
        try:
            result = self._exchange.get_order(record.symbol, record.client_order_id)
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

    def _keep(self, record: SpotOrderRecord, error: str) -> SpotOrderRecord:
        kept = record.with_state(record.state, self._clock(), error=error)
        self._store.update(kept)
        return kept

    def _apply(self, record: SpotOrderRecord, result: SpotOrderResult) -> SpotOrderRecord:
        updated = record.with_state(
            _STATE_BY_STATUS[result.status],
            self._clock(),
            exchange_order_id=result.exchange_order_id,
            filled_qty=result.executed_qty,
            quote_qty=result.quote_qty,
            fee=result.fee,
            fee_asset=result.fee_asset,
            error=None,
        )
        self._store.update(updated)
        return updated
