"""거시 일정(CPI · 고용 발표 등) 판본 읽기 — ``macro_events`` 표.

언제 쓰나: 백테스트 G6 게이트가 '그 시점에 공개돼 있던 발표 일정' 을 볼 때.
읽고 쓰는 것: ``macro_events`` 표 (읽기만). 완전성 인증은 이 계층의 책임이 아니다
              (backtest/calendar_manifest 가 매니페스트로 따로 확인한다).
"""

from __future__ import annotations

import sqlite3

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.macro import ScheduledMacroEvent

__all__ = ["MacroEventRepository"]


class MacroEventRepository:
    """거시 일정 행을 인과적으로 읽되, 행 수로 coverage를 만들지 않는다."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def schedule_revisions(
        self, *, indicators: frozenset[str], as_of_ms: int
    ) -> tuple[ScheduledMacroEvent, ...]:
        """[쓰임] 고른 지표의 발표 일정 중 ``as_of_ms`` 까지 공개된 판본만 (지표 · 기간 · 판본 순).
        [이유] 일정도 나중에 바뀐다 — 그때 몰랐던 새 판본을 쓰면 미래를 보는 셈이다. ``period`` 는
               같은 발표의 판본을 묶는 키라, 비어 있으면 일정을 추측하지 않고 오류를 낸다."""
        if not indicators or as_of_ms <= 0:
            raise ValueError("조회할 이벤트 종류와 양수 as_of_ms가 필요하다")
        placeholders = ",".join("?" for _ in indicators)
        rows = self._conn.execute(
            "SELECT indicator, period, scheduled_ms, vintage_ms, source"
            " FROM macro_events"
            f" WHERE indicator IN ({placeholders}) AND vintage_ms <= ?"
            " ORDER BY indicator, period, vintage_ms",
            (*sorted(indicators), as_of_ms),
        ).fetchall()
        events: list[ScheduledMacroEvent] = []
        for row in rows:
            period = row["period"]
            source = row["source"]
            if not isinstance(period, str) or not period.strip():
                raise DataIntegrityError("macro_events.period가 없어 일정 판본을 묶을 수 없다")
            if not isinstance(source, str) or not source.strip():
                raise DataIntegrityError("macro_events.source가 없어 원천을 추적할 수 없다")
            try:
                events.append(
                    ScheduledMacroEvent(
                        indicator=str(row["indicator"]),
                        event_key=period,
                        scheduled_ms=int(row["scheduled_ms"]),
                        vintage_ms=int(row["vintage_ms"]),
                        source=source,
                    )
                )
            except ValueError as exc:
                raise DataIntegrityError("macro_events에 유효하지 않은 일정 시각이 있다") from exc
        return tuple(events)
