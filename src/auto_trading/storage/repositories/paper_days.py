"""Paper 일일 기록 저장소 (``paper_days``, 마이그레이션 005)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = ["PaperDay", "PaperDayRepository"]


@dataclass(frozen=True, slots=True)
class PaperDay:
    strategy_id: str
    decision_ms: int
    status: str
    equity: Decimal
    holdings: dict[str, str]
    prices: dict[str, str]
    targets: dict[str, str | None]
    executed: list[dict[str, str]]
    note: str | None
    created_ms: int


def _row(day: PaperDay) -> tuple[Any, ...]:
    return (
        day.strategy_id,
        day.decision_ms,
        day.status,
        str(day.equity),
        json.dumps(day.holdings, sort_keys=True),
        json.dumps(day.prices, sort_keys=True),
        json.dumps(day.targets, sort_keys=True),
        json.dumps(day.executed, sort_keys=True),
        day.note,
        day.created_ms,
    )


def _day(row: sqlite3.Row | tuple[Any, ...]) -> PaperDay:
    return PaperDay(
        strategy_id=str(row[0]),
        decision_ms=int(row[1]),
        status=str(row[2]),
        equity=Decimal(str(row[3])),
        holdings=json.loads(row[4]),
        prices=json.loads(row[5]),
        targets=json.loads(row[6]),
        executed=json.loads(row[7]),
        note=None if row[8] is None else str(row[8]),
        created_ms=int(row[9]),
    )


_COLUMNS = (
    "strategy_id, decision_ms, status, equity, holdings_json, prices_json, targets_json,"
    " executed_json, note, created_ms"
)


class PaperDayRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, strategy_id: str, decision_ms: int) -> PaperDay | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_days WHERE strategy_id = ? AND decision_ms = ?",
            (strategy_id, decision_ms),
        ).fetchone()
        return None if row is None else _day(row)

    def insert(self, day: PaperDay) -> None:
        self._conn.execute(
            f"INSERT INTO paper_days ({_COLUMNS}) VALUES ({','.join('?' * 10)})", _row(day)
        )
        self._conn.commit()

    def recent(self, strategy_id: str, limit: int = 30) -> list[PaperDay]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_days WHERE strategy_id = ?"
            " ORDER BY decision_ms DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
        return [_day(r) for r in rows]
