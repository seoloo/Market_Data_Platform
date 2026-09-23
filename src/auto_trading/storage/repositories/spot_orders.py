"""현물 주문 기록 저장소 (``spot_orders``, 마이그레이션 004).

쓰기는 행 단위로 즉시 커밋한다. 선기록(PENDING)이 거래소 호출 전에 디스크에 있어야
재시작 뒤 복구할 수 있다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

__all__ = ["OPEN_STATES", "TERMINAL_STATES", "SpotOrderRecord", "SpotOrderRepository"]

OPEN_STATES = frozenset({"PENDING", "SUBMITTED", "UNKNOWN", "PARTIALLY_FILLED"})
TERMINAL_STATES = frozenset({"FILLED", "EXPIRED", "CANCELED", "REJECTED", "NOT_PLACED"})


@dataclass(frozen=True, slots=True)
class SpotOrderRecord:
    client_order_id: str
    mode: str
    strategy_id: str
    decision_ms: int
    symbol: str
    side: str
    qty: Decimal
    target_weight: Decimal
    state: str
    created_ms: int
    updated_ms: int
    exchange_order_id: str | None = None
    filled_qty: Decimal = Decimal(0)
    quote_qty: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    fee_asset: str | None = None
    error: str | None = None

    def with_state(self, state: str, updated_ms: int, **changes: Any) -> SpotOrderRecord:
        return replace(self, state=state, updated_ms=updated_ms, **changes)


_COLUMNS = (
    "client_order_id, mode, strategy_id, decision_ms, symbol, side, qty, target_weight, state,"
    " created_ms, updated_ms, exchange_order_id, filled_qty, quote_qty, fee, fee_asset, error"
)


def _row(record: SpotOrderRecord) -> tuple[object, ...]:
    return (
        record.client_order_id,
        record.mode,
        record.strategy_id,
        record.decision_ms,
        record.symbol,
        record.side,
        str(record.qty),
        str(record.target_weight),
        record.state,
        record.created_ms,
        record.updated_ms,
        record.exchange_order_id,
        str(record.filled_qty),
        str(record.quote_qty),
        str(record.fee),
        record.fee_asset,
        record.error,
    )


def _record(row: sqlite3.Row | tuple[Any, ...]) -> SpotOrderRecord:
    return SpotOrderRecord(
        client_order_id=str(row[0]),
        mode=str(row[1]),
        strategy_id=str(row[2]),
        decision_ms=int(row[3]),
        symbol=str(row[4]),
        side=str(row[5]),
        qty=Decimal(str(row[6])),
        target_weight=Decimal(str(row[7])),
        state=str(row[8]),
        created_ms=int(row[9]),
        updated_ms=int(row[10]),
        exchange_order_id=None if row[11] is None else str(row[11]),
        filled_qty=Decimal(str(row[12])),
        quote_qty=Decimal(str(row[13])),
        fee=Decimal(str(row[14])),
        fee_asset=None if row[15] is None else str(row[15]),
        error=None if row[16] is None else str(row[16]),
    )


class SpotOrderRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, client_order_id: str) -> SpotOrderRecord | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM spot_orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return None if row is None else _record(row)

    def insert(self, record: SpotOrderRecord) -> None:
        """선기록. 같은 ID가 있으면 IntegrityError — 호출자는 먼저 get으로 확인한다."""
        placeholders = ",".join("?" * 17)
        self._conn.execute(
            f"INSERT INTO spot_orders ({_COLUMNS}) VALUES ({placeholders})", _row(record)
        )
        self._conn.commit()

    def update(self, record: SpotOrderRecord) -> None:
        cursor = self._conn.execute(
            "UPDATE spot_orders SET state = ?, updated_ms = ?, exchange_order_id = ?,"
            " filled_qty = ?, quote_qty = ?, fee = ?, fee_asset = ?, error = ?"
            " WHERE client_order_id = ?",
            (
                record.state,
                record.updated_ms,
                record.exchange_order_id,
                str(record.filled_qty),
                str(record.quote_qty),
                str(record.fee),
                record.fee_asset,
                record.error,
                record.client_order_id,
            ),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError(f"갱신할 주문 기록이 없다: {record.client_order_id}")
        self._conn.commit()

    def unresolved(self) -> list[SpotOrderRecord]:
        placeholders = ",".join("?" * len(OPEN_STATES))
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM spot_orders WHERE state IN ({placeholders})"
            " ORDER BY created_ms, client_order_id",
            tuple(sorted(OPEN_STATES)),
        ).fetchall()
        return [_record(r) for r in rows]

    def all(self) -> list[SpotOrderRecord]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM spot_orders ORDER BY created_ms, client_order_id"
        ).fetchall()
        return [_record(r) for r in rows]
