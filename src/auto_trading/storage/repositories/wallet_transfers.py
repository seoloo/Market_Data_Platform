"""지갑 이체 기록 저장소 (``wallet_transfers``, 마이그레이션 009).

쓰기는 행 단위로 즉시 커밋한다.
선기록(PENDING)이 거래소 호출 전에 디스크에 있어야 재시작 뒤 해소할 수 있다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

__all__ = ["OPEN_TRANSFER_STATES", "TransferRecord", "WalletTransferRepository"]

OPEN_TRANSFER_STATES = frozenset({"PENDING", "UNKNOWN"})


@dataclass(frozen=True, slots=True)
class TransferRecord:
    intent_id: str
    mode: str
    strategy_id: str
    decision_ms: int
    direction: str
    asset: str
    amount: Decimal
    state: str
    created_ms: int
    updated_ms: int
    tran_id: str | None = None
    error: str | None = None

    def with_state(self, state: str, updated_ms: int, **changes: Any) -> TransferRecord:
        return replace(self, state=state, updated_ms=updated_ms, **changes)


_COLUMNS = (
    "intent_id, mode, strategy_id, decision_ms, direction, asset, amount, state, tran_id,"
    " error, created_ms, updated_ms"
)


def _record(row: sqlite3.Row | tuple[Any, ...]) -> TransferRecord:
    return TransferRecord(
        intent_id=str(row[0]),
        mode=str(row[1]),
        strategy_id=str(row[2]),
        decision_ms=int(row[3]),
        direction=str(row[4]),
        asset=str(row[5]),
        amount=Decimal(str(row[6])),
        state=str(row[7]),
        tran_id=None if row[8] is None else str(row[8]),
        error=None if row[9] is None else str(row[9]),
        created_ms=int(row[10]),
        updated_ms=int(row[11]),
    )


class WalletTransferRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, intent_id: str) -> TransferRecord | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM wallet_transfers WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        return None if row is None else _record(row)

    def insert(self, r: TransferRecord) -> None:
        """선기록. 같은 ID가 있으면 IntegrityError — 호출자는 먼저 get으로 확인한다."""
        self._conn.execute(
            f"INSERT INTO wallet_transfers ({_COLUMNS}) VALUES ({','.join('?' * 12)})",
            (
                r.intent_id,
                r.mode,
                r.strategy_id,
                r.decision_ms,
                r.direction,
                r.asset,
                str(r.amount),
                r.state,
                r.tran_id,
                r.error,
                r.created_ms,
                r.updated_ms,
            ),
        )
        self._conn.commit()

    def update(self, r: TransferRecord) -> None:
        cursor = self._conn.execute(
            "UPDATE wallet_transfers SET state = ?, tran_id = ?, error = ?, updated_ms = ?"
            " WHERE intent_id = ?",
            (r.state, r.tran_id, r.error, r.updated_ms, r.intent_id),
        )
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError(f"갱신할 이체 기록이 없다: {r.intent_id}")
        self._conn.commit()

    def open(self) -> list[TransferRecord]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM wallet_transfers WHERE state IN ('PENDING', 'UNKNOWN')"
            " ORDER BY created_ms, intent_id"
        ).fetchall()
        return [_record(r) for r in rows]

    def all(self) -> list[TransferRecord]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM wallet_transfers ORDER BY created_ms, intent_id"
        ).fetchall()
        return [_record(r) for r in rows]
