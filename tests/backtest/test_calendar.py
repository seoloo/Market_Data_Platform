"""G6 일정의 커버리지·공개 시각·블랙아웃 경계 검사."""

from __future__ import annotations

import pytest

from auto_trading.backtest.calendar import (
    CalendarCoverage,
    CoverageGap,
    MacroCalendar,
    ScheduledMacroEvent,
)
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


def test_gap_makes_calendar_unavailable_with_inclusive_bounds() -> None:
    gap = CoverageGap(50 * MIN, 60 * MIN, frozenset({"CPI"}), "사전 시각 판본 없음")
    coverage = CalendarCoverage(1, 100 * MIN, frozenset({"CPI"}), "audited-source", (gap,))
    calendar = MacroCalendar((_event(),), coverage)
    assert calendar.status_at(50 * MIN - 1, WINDOWS) == (True, False)
    assert calendar.status_at(50 * MIN, WINDOWS) == (False, False)
    assert calendar.status_at(60 * MIN, WINDOWS) == (False, False)
    assert calendar.status_at(60 * MIN + 1, WINDOWS) == (True, False)


def test_gap_of_unrequested_indicator_is_ignored() -> None:
    gap = CoverageGap(50 * MIN, 60 * MIN, frozenset({"POWELL"}), "사전 시각 판본 없음")
    coverage = CalendarCoverage(
        1, 100 * MIN, frozenset({"CPI", "POWELL"}), "audited-source", (gap,)
    )
    assert MacroCalendar((_event(),), coverage).status_at(55 * MIN, WINDOWS) == (True, False)


def test_invalid_gaps_are_rejected() -> None:
    with pytest.raises(ValueError, match="구간"):
        CoverageGap(60 * MIN, 50 * MIN, frozenset({"CPI"}), "x")
    with pytest.raises(ValueError, match="사유"):
        CoverageGap(50 * MIN, 60 * MIN, frozenset({"CPI"}), "")
    outside_kind = CoverageGap(50 * MIN, 60 * MIN, frozenset({"FOMC"}), "x")
    with pytest.raises(ValueError, match="종류"):
        CalendarCoverage(1, 100 * MIN, frozenset({"CPI"}), "audited", (outside_kind,))
    outside_range = CoverageGap(90 * MIN, 110 * MIN, frozenset({"CPI"}), "x")
    with pytest.raises(ValueError, match="벗어"):
        CalendarCoverage(1, 100 * MIN, frozenset({"CPI"}), "audited", (outside_range,))
