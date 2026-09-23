"""G6 달력 매니페스트 — 감사된 거시 일정·감사 범위·공백을 한 파일로 고정한다.

감사 스크립트(scripts/audit_*_schedule.py)의 결과를 모아 만든다. 매니페스트의
digest가 :class:`CalendarCoverage`의 ``source_digest``가 되므로, 실행 시 DB에서 읽은
일정이 이 매니페스트와 다르면 coverage를 만들 수 없다. 행 개수만으로 coverage를
만들지 않는다는 원칙(05 원천 감사)을 파일 단위로 강제한다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from auto_trading.backtest.calendar import CalendarCoverage, CoverageGap, MacroCalendar
from auto_trading.core.hashing import sha256_hex
from auto_trading.core.macro import ScheduledMacroEvent

__all__ = ["CalendarEntry", "CalendarManifest"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """감사를 통과한 일정 한 건. ``vintage_ms``는 그 일정이 공개돼 있던 시각이다."""

    indicator: str
    event_key: str
    scheduled_ms: int
    vintage_ms: int
    released_ms: int | None
    source: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.indicator or not self.event_key or not self.source:
            raise ValueError("달력 항목에는 종류·이벤트 키·원천이 필요하다")
        if not _SHA256.fullmatch(self.source_sha256):
            raise ValueError(f"원천 SHA-256 형식이 아니다: {self.indicator} {self.event_key}")
        if not 0 < self.vintage_ms < self.scheduled_ms:
            raise ValueError(f"일정 판본은 일정보다 먼저 공개돼야 한다: {self.event_key}")
        if self.released_ms is not None and self.released_ms <= 0:
            raise ValueError(f"실제 발표 시각이 유효하지 않다: {self.event_key}")

    @property
    def db_source(self) -> str:
        """``macro_events.source``에 저장하는 값. 해시 열이 없으므로 원천에 붙인다."""
        return f"{self.source}#sha256={self.source_sha256}"

    def to_event(self) -> ScheduledMacroEvent:
        return ScheduledMacroEvent(
            indicator=self.indicator,
            event_key=self.event_key,
            scheduled_ms=self.scheduled_ms,
            vintage_ms=self.vintage_ms,
            source=self.db_source,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "indicator": self.indicator,
            "event_key": self.event_key,
            "scheduled_ms": self.scheduled_ms,
            "vintage_ms": self.vintage_ms,
            "released_ms": self.released_ms,
            "source": self.source,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class CalendarManifest:
    """감사 범위 하나의 완전한 일정 집합."""

    name: str
    from_ms: int
    to_ms: int
    indicators: frozenset[str]
    gaps: tuple[CoverageGap, ...]
    entries: tuple[CalendarEntry, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("매니페스트 이름이 필요하다")
        keys = [(entry.indicator, entry.event_key) for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("같은 종류·이벤트 키의 항목이 중복됐다")
        unknown = {entry.indicator for entry in self.entries} - self.indicators
        if unknown:
            raise ValueError(f"감사 범위에 없는 종류의 항목이 있다: {sorted(unknown)}")
        self.coverage()  # 범위·공백 검증을 생성 시점에 한 번 수행한다.

    def payload(self) -> dict[str, Any]:
        entries = sorted(self.entries, key=lambda entry: (entry.indicator, entry.scheduled_ms))
        gaps = sorted(self.gaps, key=lambda gap: (gap.from_ms, sorted(gap.indicators)))
        return {
            "name": self.name,
            "from_ms": self.from_ms,
            "to_ms": self.to_ms,
            "indicators": sorted(self.indicators),
            "gaps": [
                {
                    "from_ms": gap.from_ms,
                    "to_ms": gap.to_ms,
                    "indicators": sorted(gap.indicators),
                    "reason": gap.reason,
                }
                for gap in gaps
            ],
            "entries": [entry.payload() for entry in entries],
        }

    def digest(self) -> str:
        return sha256_hex(self.payload())

    def coverage(self) -> CalendarCoverage:
        return CalendarCoverage(
            from_ms=self.from_ms,
            to_ms=self.to_ms,
            indicators=self.indicators,
            source_digest=sha256_hex(self.payload()),
            gaps=self.gaps,
        )

    def events(self) -> tuple[ScheduledMacroEvent, ...]:
        return tuple(entry.to_event() for entry in self.entries)

    def to_calendar(self) -> MacroCalendar:
        return MacroCalendar(self.events(), self.coverage())

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload(), "digest": self.digest()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalendarManifest:
        """파일에서 읽는다. 기록된 digest와 내용이 다르면 거부한다."""
        manifest = cls(
            name=str(data["name"]),
            from_ms=int(data["from_ms"]),
            to_ms=int(data["to_ms"]),
            indicators=frozenset(str(value) for value in data["indicators"]),
            gaps=tuple(
                CoverageGap(
                    from_ms=int(gap["from_ms"]),
                    to_ms=int(gap["to_ms"]),
                    indicators=frozenset(str(value) for value in gap["indicators"]),
                    reason=str(gap["reason"]),
                )
                for gap in data["gaps"]
            ),
            entries=tuple(
                CalendarEntry(
                    indicator=str(entry["indicator"]),
                    event_key=str(entry["event_key"]),
                    scheduled_ms=int(entry["scheduled_ms"]),
                    vintage_ms=int(entry["vintage_ms"]),
                    released_ms=(
                        None if entry["released_ms"] is None else int(entry["released_ms"])
                    ),
                    source=str(entry["source"]),
                    source_sha256=str(entry["source_sha256"]),
                )
                for entry in data["entries"]
            ),
        )
        recorded = data.get("digest")
        if recorded != manifest.digest():
            raise ValueError("매니페스트 digest가 내용과 다르다")
        return manifest
