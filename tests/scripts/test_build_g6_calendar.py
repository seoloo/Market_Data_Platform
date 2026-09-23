"""G6 매니페스트 빌더의 감사 규칙과 적재 dry-run 계획 검사."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from scripts.audit_fomc_schedule import STATEMENT_DATES
from scripts.audit_powell_press_schedule import unknown_day_gap
from scripts.build_g6_calendar import plan_load, validate

from auto_trading.backtest.calendar import CoverageGap
from auto_trading.backtest.calendar_manifest import CalendarEntry, CalendarManifest
from auto_trading.config.schema import BlackoutWindow

SHA = "b" * 64
HOUR = 3_600_000
WINDOWS = {
    name: BlackoutWindow(before_min=30, after_min=15)
    for name in ("FOMC", "CPI", "PCE", "NFP", "POWELL")
}


def _ms(year: int, month: int, day: int, hour: int = 13) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1000)


def _entry(indicator: str, key: str, scheduled_ms: int) -> CalendarEntry:
    return CalendarEntry(indicator, key, scheduled_ms, scheduled_ms - 24 * HOUR, None, "src", SHA)


def _monthly(indicator: str) -> list[CalendarEntry]:
    return [
        _entry(indicator, f"{year}-{month:02d}", _ms(year, month, 10))
        for year in (2021, 2022)
        for month in range(1, 13)
    ]


def _meetings(indicator: str, *, skip: frozenset[str] = frozenset()) -> list[CalendarEntry]:
    return [
        _entry(indicator, f"{day:%Y-%m}", _ms(day.year, day.month, day.day, 19))
        for day in STATEMENT_DATES
        if f"{day:%Y-%m}" not in skip
    ]


def _complete() -> tuple[list[CalendarEntry], list[CoverageGap]]:
    gap_day = STATEMENT_DATES[0]
    start, end = unknown_day_gap(gap_day)
    gaps = [CoverageGap(start, end, frozenset({"POWELL"}), "판본 없음")]
    entries = [
        *_monthly("CPI"),
        *_monthly("PCE"),
        *_monthly("NFP"),
        *_meetings("FOMC"),
        *_meetings("POWELL", skip=frozenset({f"{gap_day:%Y-%m}"})),
    ]
    return entries, gaps


def test_complete_calendar_passes_and_counts_train_events() -> None:
    entries, gaps = _complete()
    counts = validate(entries, gaps, WINDOWS)
    assert counts == {"FOMC": 16, "CPI": 24, "PCE": 24, "NFP": 24, "POWELL": 15}


def test_vintage_after_blackout_start_is_rejected() -> None:
    entries, gaps = _complete()
    late = entries[0]
    entries[0] = CalendarEntry(
        late.indicator,
        late.event_key,
        late.scheduled_ms,
        late.scheduled_ms - 10 * 60_000,
        None,
        "src",
        SHA,
    )
    with pytest.raises(ValueError, match="블랙아웃 시작 뒤"):
        validate(entries, gaps, WINDOWS)


def test_missing_month_is_rejected() -> None:
    entries, gaps = _complete()
    entries = [e for e in entries if not (e.indicator == "NFP" and e.event_key == "2021-06")]
    with pytest.raises(ValueError, match="빠진 달"):
        validate(entries, gaps, WINDOWS)


def test_press_conferences_plus_gaps_must_equal_meetings() -> None:
    entries, _ = _complete()
    with pytest.raises(ValueError, match="기자회견"):
        validate(entries, [], WINDOWS)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE macro_events (indicator TEXT, scheduled_ms INTEGER, released_ms INTEGER,"
        " period TEXT, source TEXT, vintage_ms INTEGER)"
    )
    yield connection
    connection.close()


def test_plan_load_separates_new_identical_and_conflicting_rows(conn: sqlite3.Connection) -> None:
    fresh, same, conflict = (
        _entry("CPI", "2021-01", _ms(2021, 1, 13)),
        _entry("CPI", "2021-02", _ms(2021, 2, 10)),
        _entry("CPI", "2021-03", _ms(2021, 3, 10)),
    )
    manifest = CalendarManifest(
        "t", 1, _ms(2023, 1, 1), frozenset({"CPI"}), (), (fresh, same, conflict)
    )
    for entry, scheduled in ((same, same.scheduled_ms), (conflict, conflict.scheduled_ms + 1)):
        conn.execute(
            "INSERT INTO macro_events VALUES (?, ?, ?, ?, ?, ?)",
            (entry.indicator, scheduled, None, entry.event_key, entry.db_source, entry.vintage_ms),
        )
    planned, identical, conflicts = plan_load(manifest, conn)
    assert planned == [fresh]
    assert identical == 1
    assert conflicts == ["CPI 2021-03"]
