"""과거 판단 시각에 알려진 거시 발표 일정만으로 G6 블랙아웃을 계산한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from auto_trading.config.schema import BlackoutWindow
from auto_trading.core.macro import ScheduledMacroEvent

__all__ = ["CalendarCoverage", "CoverageGap", "MacroCalendar", "ScheduledMacroEvent"]

_MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """감사 범위 안에서 사전 공개 근거를 찾지 못한 구간. 이 구간은 달력 사용 불가다.

    시각을 모르는 일정을 관행으로 채우면 사후 추론이 되므로, 그 구간 전체를
    fail-closed로 둔다. 예: 기자회견 개최는 알려졌으나 시각의 사전 판본이 없는 날.
    """

    from_ms: int
    to_ms: int
    indicators: frozenset[str]
    reason: str

    def __post_init__(self) -> None:
        if self.to_ms <= self.from_ms:
            raise ValueError("달력 공백 구간이 유효하지 않다")
        if not self.indicators or not self.reason:
            raise ValueError("달력 공백에는 이벤트 종류와 사유가 필요하다")


@dataclass(frozen=True, slots=True)
class CalendarCoverage:
    """독립 감사로 확인한 일정 범위. 이벤트 행 개수만으로 만들 수 없다."""

    from_ms: int
    to_ms: int
    indicators: frozenset[str]
    source_digest: str
    gaps: tuple[CoverageGap, ...] = ()

    def __post_init__(self) -> None:
        if self.from_ms <= 0 or self.to_ms <= self.from_ms:
            raise ValueError("달력 감사 범위가 유효하지 않다")
        if not self.indicators or not self.source_digest:
            raise ValueError("감사한 이벤트 종류와 원천 digest가 필요하다")
        for gap in self.gaps:
            if not gap.indicators <= self.indicators:
                raise ValueError("달력 공백의 이벤트 종류가 감사 범위에 없다")
            if gap.from_ms < self.from_ms or gap.to_ms > self.to_ms:
                raise ValueError("달력 공백이 감사 범위를 벗어난다")

    def unavailable_at(self, as_of_ms: int, indicators: frozenset[str]) -> bool:
        """``as_of_ms``가 요청한 종류의 공백 안이면 참이다. 경계는 포함한다."""
        return any(
            gap.from_ms <= as_of_ms <= gap.to_ms and not gap.indicators.isdisjoint(indicators)
            for gap in self.gaps
        )


class MacroCalendar:
    """완전성은 coverage가, 개별 일정의 인과성은 vintage가 보장한다."""

    __slots__ = ("_coverage", "_events")

    def __init__(
        self,
        events: tuple[ScheduledMacroEvent, ...] = (),
        coverage: CalendarCoverage | None = None,
    ) -> None:
        self._events = events
        self._coverage = coverage

    def status_at(self, as_of_ms: int, windows: Mapping[str, BlackoutWindow]) -> tuple[bool, bool]:
        """(달력 사용 가능, 블랙아웃). 감사 공백은 항상 사용 불가."""
        coverage = self._coverage
        if (
            coverage is None
            or not coverage.from_ms <= as_of_ms <= coverage.to_ms
            or not set(windows).issubset(coverage.indicators)
            or coverage.unavailable_at(as_of_ms, frozenset(windows))
        ):
            return False, False

        # 일정 변경은 시점마다 알려진 최신 판본만 사용한다.
        known: dict[tuple[str, str], ScheduledMacroEvent] = {}
        for event in self._events:
            if event.vintage_ms > as_of_ms or event.indicator not in windows:
                continue
            key = (event.indicator, event.event_key)
            previous = known.get(key)
            if previous is None or event.vintage_ms > previous.vintage_ms:
                known[key] = event
        for event in known.values():
            window = windows[event.indicator]
            if (
                event.scheduled_ms - window.before_min * _MINUTE_MS
                <= as_of_ms
                <= event.scheduled_ms + window.after_min * _MINUTE_MS
            ):
                return True, True
        return True, False
