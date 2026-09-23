"""G6의 시점별 거시 일정 판본을 읽는다. 완전성 인증은 이 계층의 책임이 아니다."""

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
        """감사 구간 끝까지 공개된 판본만 반환한다.

        ``period``는 동일 이벤트의 판본을 묶는 영속 키다. 비어 있다면
        일정을 임의로 추론하지 않고 데이터 오류로 거부한다.
        """
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
