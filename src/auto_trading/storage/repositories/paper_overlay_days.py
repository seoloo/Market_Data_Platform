"""O/v1 Paper 가상 원장 저장소 (``paper_overlay_days``, 마이그레이션 006)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["OverlayPaperDay", "OverlayPaperDayRepository"]


@dataclass(frozen=True, slots=True)
class OverlayPaperDay:
    strategy_id: str
    decision_ms: int
    status: str
    equity: float
    state: dict[str, Any]
    tally: dict[str, Any]
    market: dict[str, Any]
    fills: list[dict[str, Any]]
    observed: dict[str, Any]
    note: str | None
    created_ms: int


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _row(day: OverlayPaperDay) -> tuple[Any, ...]:
    return (
        day.strategy_id,
        day.decision_ms,
        day.status,
        repr(day.equity),
        _dumps(day.state),
        _dumps(day.tally),
        _dumps(day.market),
        _dumps(day.fills),
        _dumps(day.observed),
        day.note,
        day.created_ms,
    )


def _day(row: sqlite3.Row | tuple[Any, ...]) -> OverlayPaperDay:
    return OverlayPaperDay(
        strategy_id=str(row[0]),
        decision_ms=int(row[1]),
        status=str(row[2]),
        equity=float(row[3]),
        state=json.loads(row[4]),
        tally=json.loads(row[5]),
        market=json.loads(row[6]),
        fills=json.loads(row[7]),
        observed=json.loads(row[8]),
        note=None if row[9] is None else str(row[9]),
        created_ms=int(row[10]),
    )


_COLUMNS = (
    "strategy_id, decision_ms, status, equity, state_json, tally_json, market_json,"
    " fills_json, observed_json, note, created_ms"
)


class OverlayPaperDayRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, strategy_id: str, decision_ms: int) -> OverlayPaperDay | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ? AND decision_ms = ?",
            (strategy_id, decision_ms),
        ).fetchone()
        return None if row is None else _day(row)

    def latest(self, strategy_id: str) -> OverlayPaperDay | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ?"
            " ORDER BY decision_ms DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
        return None if row is None else _day(row)

    def insert_all(self, days: Sequence[OverlayPaperDay]) -> None:
        """여러 날을 **한 트랜잭션**으로 쓴다. 하나라도 실패하면 아무것도 남지 않는다."""
        placeholders = ",".join("?" * 11)
        try:
            self._conn.execute("BEGIN")
            for day in days:
                self._conn.execute(
                    f"INSERT INTO paper_overlay_days ({_COLUMNS}) VALUES ({placeholders})",
                    _row(day),
                )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def recent(self, strategy_id: str, limit: int = 30) -> list[OverlayPaperDay]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ?"
            " ORDER BY decision_ms DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
        return [_day(r) for r in rows]

    def all(self, strategy_id: str) -> list[OverlayPaperDay]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ? ORDER BY decision_ms",
            (strategy_id,),
        ).fetchall()
        return [_day(r) for r in rows]
