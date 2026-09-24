"""위험 이벤트 기록 (``risk_events``, 마이그레이션 001).

재시작 정합성 복구에서 찾은 차이와 취한 조치를 남긴다 (TRADING_CORE §재시작·정합성 복구).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

__all__ = ["RiskEvent", "RiskEventRepository"]


@dataclass(frozen=True, slots=True)
class RiskEvent:
    ts_ms: int
    mode: str
    severity: str
    """info | warn | critical"""
    kind: str
    detail: dict[str, Any]
    action_taken: str | None = None


class RiskEventRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, event: RiskEvent) -> None:
        self._conn.execute(
            "INSERT INTO risk_events (ts_ms, mode, severity, kind, detail_json, action_taken)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.ts_ms,
                event.mode,
                event.severity,
                event.kind,
                json.dumps(event.detail, sort_keys=True, ensure_ascii=False, default=str),
                event.action_taken,
            ),
        )
        self._conn.commit()

    def recent(self, limit: int = 50) -> list[RiskEvent]:
        rows = self._conn.execute(
            "SELECT ts_ms, mode, severity, kind, detail_json, action_taken FROM risk_events"
            " ORDER BY ts_ms DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            RiskEvent(int(r[0]), str(r[1]), str(r[2]), str(r[3]), json.loads(r[4]), r[5])
            for r in rows
        ]
