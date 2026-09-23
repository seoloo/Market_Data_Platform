"""거시 발표 일정의 공급자·저장소 비종속 자료형."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScheduledMacroEvent:
    indicator: str
    event_key: str
    scheduled_ms: int
    vintage_ms: int
    source: str

    def __post_init__(self) -> None:
        if not self.indicator or not self.event_key or not self.source:
            raise ValueError("거시 일정은 종류·이벤트 키·원천이 필요하다")
        if self.scheduled_ms <= 0 or self.vintage_ms <= 0:
            raise ValueError("거시 일정 시각은 양수 UTC epoch ms 여야 한다")
