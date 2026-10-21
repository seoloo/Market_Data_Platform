"""O/v1 Paper(가상 장부) 일일 기록 저장소 — ``paper_overlay_days`` 표 (마이그레이션 006).

언제 쓰나: 오버레이 Paper 실행기가 하루 기록을 남기고, 점검 · 화면이 최근 기록을 읽을 때.
읽고 쓰는 것: ``paper_overlay_days`` 표. 상태 · 집계 · 시장 · 체결 · 관측은 JSON 문자열 열.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["OverlayPaperDay", "OverlayPaperDayRepository"]


@dataclass(frozen=True, slots=True)
class OverlayPaperDay:
    """오버레이 Paper 하루 기록 한 줄."""

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


def _to_json_text(value: object) -> str:
    """[쓰임] 값을 키 정렬 JSON 문자열로 바꾼다. NaN · 무한대가 있으면 오류(ValueError).
    [이유] NaN 이 장부에 들어가면 다음 날 계산이 조용히 망가진다 — 쓰는 순간 막는다."""
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _day_to_row(day: OverlayPaperDay) -> tuple[Any, ...]:
    """[쓰임] :class:`OverlayPaperDay` 를 표에 넣을 값 묶음으로 바꾼다 (열 순서 = ``_COLUMNS``).
    [이유] 평가액은 ``repr`` 로 저장한다 — float 를 다시 읽었을 때 정확히 같은 값이 되게."""
    return (
        day.strategy_id,
        day.decision_ms,
        day.status,
        repr(day.equity),
        _to_json_text(day.state),
        _to_json_text(day.tally),
        _to_json_text(day.market),
        _to_json_text(day.fills),
        _to_json_text(day.observed),
        day.note,
        day.created_ms,
    )


def _row_to_day(row: sqlite3.Row | tuple[Any, ...]) -> OverlayPaperDay:
    """[쓰임] 표 한 줄(``_COLUMNS`` 순서)을 :class:`OverlayPaperDay` 로 되돌린다."""
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
"""읽기 · 쓰기에 같이 쓰는 열 목록 — 위 두 함수의 값 순서와 반드시 같아야 한다."""


class OverlayPaperDayRepository:
    """``paper_overlay_days`` 표 읽기 · 쓰기 창구."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, strategy_id: str, decision_ms: int) -> OverlayPaperDay | None:
        """[쓰임] 전략 · 판단 시각으로 하루 기록 하나를 찾는다 (없으면 None)."""
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ? AND decision_ms = ?",
            (strategy_id, decision_ms),
        ).fetchone()
        return None if row is None else _row_to_day(row)

    def latest(self, strategy_id: str) -> OverlayPaperDay | None:
        """[쓰임] 가장 최근 하루 기록 (없으면 None) — 다음 날 계산의 출발점."""
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ?"
            " ORDER BY decision_ms DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
        return None if row is None else _row_to_day(row)

    def insert_all(self, days: Sequence[OverlayPaperDay]) -> None:
        """[쓰임] 여러 날을 **한 트랜잭션**으로 쓴다.
        [이유] 놓친 날을 몰아서 쓸 때 하나라도 실패하면 아무것도 남지 않아야, 중간이 빈 장부가
               생기지 않는다."""
        placeholders = ",".join("?" * 11)
        try:
            self._conn.execute("BEGIN")
            for day in days:
                self._conn.execute(
                    f"INSERT INTO paper_overlay_days ({_COLUMNS}) VALUES ({placeholders})",
                    _day_to_row(day),
                )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def recent(self, strategy_id: str, limit: int = 30) -> list[OverlayPaperDay]:
        """[쓰임] 최근 기록 ``limit`` 개를 최신 순서로 돌려준다."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ?"
            " ORDER BY decision_ms DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
        return [_row_to_day(row) for row in rows]

    def all(self, strategy_id: str) -> list[OverlayPaperDay]:
        """[쓰임] 그 전략의 모든 기록을 오래된 순서로 돌려준다 (장부 전체 재계산 · 대조용)."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM paper_overlay_days WHERE strategy_id = ? ORDER BY decision_ms",
            (strategy_id,),
        ).fetchall()
        return [_row_to_day(row) for row in rows]
