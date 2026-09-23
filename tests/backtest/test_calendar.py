"""G6 일정의 커버리지·공개 시각·블랙아웃 경계 검사."""

from __future__ import annotations

from auto_trading.backtest.calendar import CalendarCoverage, MacroCalendar, ScheduledMacroEvent
from auto_trading.config.schema import BlackoutWindow

MIN = 60_000
WINDOWS = {"CPI": BlackoutWindow(before_min=5, after_min=10)}


def _coverage(*, indicators: frozenset[str] = frozenset({"CPI"})) -> CalendarCoverage:
    return CalendarCoverage(1, 100 * MIN, indicators, "audited-source")


def _event(*, scheduled_min: int = 30, vintage_min: int = 2) -> ScheduledMacroEvent:
    return ScheduledMacroEvent("CPI", "release-1", scheduled_min * MIN, vintage_min * MIN, "BLS")


def test_missing_coverage_and_indicator_fail_closed() -> None:
    assert MacroCalendar((_event(),)).status_at(30 * MIN, WINDOWS) == (False, False)
    missing_kind = MacroCalendar((_event(),), _coverage(indicators=frozenset({"FOMC"})))
    assert missing_kind.status_at(30 * MIN, WINDOWS) == (False, False)
    audited = MacroCalendar((_event(),), _coverage())
    assert audited.status_at(0, WINDOWS) == (False, False)
    assert audited.status_at(101 * MIN, WINDOWS) == (False, False)


def test_blackout_boundaries_are_inclusive() -> None:
    calendar = MacroCalendar((_event(),), _coverage())
    assert calendar.status_at(25 * MIN - 1, WINDOWS) == (True, False)
    assert calendar.status_at(25 * MIN, WINDOWS) == (True, True)
    assert calendar.status_at(40 * MIN, WINDOWS) == (True, True)
    assert calendar.status_at(40 * MIN + 1, WINDOWS) == (True, False)


def test_later_vintage_cannot_affect_earlier_decision() -> None:
    calendar = MacroCalendar((_event(vintage_min=31),), _coverage())
    assert calendar.status_at(25 * MIN, WINDOWS) == (True, False)
    assert calendar.status_at(31 * MIN, WINDOWS) == (True, True)


def test_schedule_revision_uses_latest_known_vintage_only() -> None:
    revised = ScheduledMacroEvent("CPI", "release-1", 60 * MIN, 20 * MIN, "BLS-revision")
    calendar = MacroCalendar((revised, _event()), _coverage())
    assert calendar.status_at(19 * MIN, WINDOWS) == (True, False)
    assert calendar.status_at(25 * MIN, WINDOWS) == (True, False)
    assert calendar.status_at(55 * MIN, WINDOWS) == (True, True)


def test_future_event_with_known_schedule_is_visible_before_release() -> None:
    calendar = MacroCalendar((_event(),), _coverage())
    assert calendar.status_at(26 * MIN, WINDOWS) == (True, True)
