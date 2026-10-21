"""Paper(가상 장부) 일일 기록 저장소 — ``paper_days`` 표 (마이그레이션 005).

언제 쓰나: 현물 Paper 실행기가 하루 한 번 그날의 판단 · 평가액 · 가상 체결을 남기고, 점검이 읽을 때.
읽고 쓰는 것: ``paper_days`` 표. 보유 · 가격 · 목표 · 체결은 JSON 문자열 열로 저장한다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = ["PaperDay", "PaperDayRepository"]


@dataclass(frozen=True, slots=True)
class PaperDay:
    """Paper 하루 기록 한 줄 (판단 시각 · 상태 · 평가액 · 보유 · 가격 · 목표 · 가상 체결 · 메모)."""

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


def _day_to_row(day: PaperDay) -> tuple[Any, ...]:
    """[쓰임] :class:`PaperDay` 를 표에 넣을 값 묶음으로 바꾼다 (열 순서는 ``_COLUMNS`` 와 같다).
    [이유] 금액은 Decimal 문자열, dict · list 는 키 정렬 JSON — 같은 기록은 늘 같은 글자."""
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


def _row_to_day(row: sqlite3.Row | tuple[Any, ...]) -> PaperDay:
    """[쓰임] 표 한 줄(``_COLUMNS`` 순서)을 :class:`PaperDay` 로 되돌린다."""
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
"""읽기 · 쓰기에 같이 쓰는 열 목록 — 위 두 함수의 값 순서와 반드시 같아야 한다."""


class PaperDayRepository:
    """``paper_days`` 표 읽기 · 쓰기 창구."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, strategy_id: str, decision_ms: int) -> PaperDay | None:
        """[쓰임] 전략 · 판단 시각으로 하루 기록 하나를 찾는다 (없으면 None)."""
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_days WHERE strategy_id = ? AND decision_ms = ?",
            (strategy_id, decision_ms),
        ).fetchone()
        return None if row is None else _row_to_day(row)

    def insert(self, day: PaperDay) -> None:
        """[쓰임] 하루 기록을 넣고 바로 커밋한다. 같은 전략 · 같은 판단 시각이 있으면 오류.
        [이유] Paper 는 'UTC 하루 한 번' 이 규칙이라, 같은 날을 두 번 쓰려는 것은 덮어쓰지 않고
               오류로 드러낸다."""
        self._conn.execute(
            f"INSERT INTO paper_days ({_COLUMNS}) VALUES ({','.join('?' * 10)})", _day_to_row(day)
        )
        self._conn.commit()

    def recent(self, strategy_id: str, limit: int = 30) -> list[PaperDay]:
        """[쓰임] 최근 기록 ``limit`` 개를 최신 순서로 돌려준다."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_days WHERE strategy_id = ?"
            " ORDER BY decision_ms DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
        return [_row_to_day(row) for row in rows]
