"""선물 주문 기록 저장소 (``futures_orders``, 마이그레이션 007·008·010).

쓰기는 행 단위로 즉시 커밋한다. 선기록(PENDING)이 거래소 호출 전에 디스크에 있어야
재시작 뒤 복구할 수 있다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

__all__ = [
    "OPEN_STATES",
    "TERMINAL_STATES",
    "FuturesOrderRecord",
    "FuturesOrderRepository",
]

OPEN_STATES = frozenset({"PENDING", "SUBMITTED", "UNKNOWN", "PARTIALLY_FILLED"})
TERMINAL_STATES = frozenset({"FILLED", "EXPIRED", "CANCELED", "REJECTED", "NOT_PLACED"})
_UNCERTAIN = frozenset({"PENDING", "UNKNOWN"})


@dataclass(frozen=True, slots=True)
class FuturesOrderRecord:
    client_order_id: str
    mode: str
    strategy_id: str
    decision_ms: int
    symbol: str
    side: str
    order_type: str
    purpose: str
    reduce_only: bool
    close_position: bool
    qty: Decimal
    state: str
    created_ms: int
    updated_ms: int
    stop_price: Decimal = Decimal(0)
    exchange_order_id: str | None = None
    filled_qty: Decimal = Decimal(0)
    avg_price: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    fee_asset: str | None = None
    error: str | None = None
    limit_price: Decimal = Decimal(0)
    """지정가(LIMIT) 주문의 가격 한도. 다른 주문은 0."""

    def with_state(self, state: str, updated_ms: int, **changes: Any) -> FuturesOrderRecord:
        return replace(self, state=state, updated_ms=updated_ms, **changes)

    @property
    def is_working_stop(self) -> bool:
        """거래소에 걸려 있는 보호 스톱 — 정상 대기 상태다."""
        return self.order_type == "STOP_MARKET" and self.state == "SUBMITTED"

    @property
    def needs_reconcile(self) -> bool:
        """결과를 모르거나 체결이 끝나지 않은 주문 — 해소 전 신규 주문 금지.

        즉시 체결 주문(MARKET, IOC LIMIT)은 곧바로 끝나야 하므로 열려 있으면 해소 대상이다."""
        if self.state in _UNCERTAIN:
            return True
        return self.order_type in {"MARKET", "LIMIT"} and self.state in OPEN_STATES


_COLUMNS = (
    "client_order_id, mode, strategy_id, decision_ms, symbol, side, order_type, purpose,"
    " reduce_only, close_position, qty, stop_price, state, exchange_order_id, filled_qty,"
    " avg_price, fee, fee_asset, error, created_ms, updated_ms, limit_price"
)


def _row(r: FuturesOrderRecord) -> tuple[object, ...]:
    return (
        r.client_order_id,
        r.mode,
        r.strategy_id,
        r.decision_ms,
        r.symbol,
        r.side,
        r.order_type,
        r.purpose,
        int(r.reduce_only),
        int(r.close_position),
        str(r.qty),
        str(r.stop_price),
        r.state,
        r.exchange_order_id,
        str(r.filled_qty),
        str(r.avg_price),
        str(r.fee),
        r.fee_asset,
        r.error,
        r.created_ms,
        r.updated_ms,
        str(r.limit_price),
    )


def _record(row: sqlite3.Row | tuple[Any, ...]) -> FuturesOrderRecord:
    return FuturesOrderRecord(
        client_order_id=str(row[0]),
        mode=str(row[1]),
        strategy_id=str(row[2]),
        decision_ms=int(row[3]),
        symbol=str(row[4]),
        side=str(row[5]),
        order_type=str(row[6]),
        purpose=str(row[7]),
        reduce_only=bool(row[8]),
        close_position=bool(row[9]),
        qty=Decimal(str(row[10])),
        stop_price=Decimal(str(row[11])),
        state=str(row[12]),
        exchange_order_id=None if row[13] is None else str(row[13]),
        filled_qty=Decimal(str(row[14])),
        avg_price=Decimal(str(row[15])),
        fee=Decimal(str(row[16])),
        fee_asset=None if row[17] is None else str(row[17]),
        error=None if row[18] is None else str(row[18]),
        created_ms=int(row[19]),
        updated_ms=int(row[20]),
        limit_price=Decimal(str(row[21])),
    )


class FuturesOrderRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, client_order_id: str) -> FuturesOrderRecord | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM futures_orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return None if row is None else _record(row)

    def insert(self, record: FuturesOrderRecord) -> None:
        """선기록. 같은 ID가 있으면 IntegrityError — 호출자는 먼저 get으로 확인한다."""
        placeholders = ",".join("?" * 22)
        self._conn.execute(
            f"INSERT INTO futures_orders ({_COLUMNS}) VALUES ({placeholders})", _row(record)
        )
        self._conn.commit()

    def update(self, record: FuturesOrderRecord) -> None:
        cursor = self._conn.execute(
            "UPDATE futures_orders SET state = ?, updated_ms = ?, exchange_order_id = ?,"
            " filled_qty = ?, avg_price = ?, fee = ?, fee_asset = ?, error = ?"
            " WHERE client_order_id = ?",
            (
                record.state,
                record.updated_ms,
                record.exchange_order_id,
                str(record.filled_qty),
                str(record.avg_price),
                str(record.fee),
                record.fee_asset,
                record.error,
                record.client_order_id,
            ),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError(f"갱신할 주문 기록이 없다: {record.client_order_id}")
        self._conn.commit()

    def open(self) -> list[FuturesOrderRecord]:
        """종결되지 않은 모든 주문 (걸린 스톱 포함)."""
        placeholders = ",".join("?" * len(OPEN_STATES))
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM futures_orders WHERE state IN ({placeholders})"
            " ORDER BY created_ms, client_order_id",
            tuple(sorted(OPEN_STATES)),
        ).fetchall()
        return [_record(r) for r in rows]

    def all(self) -> list[FuturesOrderRecord]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM futures_orders ORDER BY created_ms, client_order_id"
        ).fetchall()
        return [_record(r) for r in rows]

    # ------------------------------------------------------------ 외부 변동

    def add_adjustment(self, ts_ms: int, symbol: str, qty: Decimal, reason: str) -> None:
        """확인된 외부 포지션 변동을 남긴다 (부호 있는 수량)."""
        self._conn.execute(
            "INSERT INTO futures_position_adjustments (ts_ms, symbol, qty, reason)"
            " VALUES (?, ?, ?, ?)",
            (ts_ms, symbol, str(qty), reason),
        )
        self._conn.commit()

    def adjustments(self) -> list[tuple[str, Decimal]]:
        rows = self._conn.execute(
            "SELECT symbol, qty FROM futures_position_adjustments ORDER BY id"
        ).fetchall()
        return [(str(r[0]), Decimal(str(r[1]))) for r in rows]
