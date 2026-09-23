"""FOMC 캘린더 스냅샷·성명 보도자료 파서의 시각·서머타임·실패 검사."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from scripts.audit_fomc_schedule import (
    STATEMENT_DATES,
    AuditRow,
    _cutoff,
    calendar_statement_ms,
    statement_release_ms,
)


def _panel(clock: str, body: str, days: str) -> str:
    return (
        '<div class="row"><div class="panel-body"><div class="row">'
        f'<div class="col-xs-2">\n<p>{clock}</p>\n</div>\n'
        f'<div class="col-xs-7">\n<p>{body}</p>\n</div>\n'
        f'<div class="col-xs-3">\n<p>{days}</p>\n</div>'
        "</div></div></div>"
    )


def _calendar(*panels: str) -> str:
    return (
        '<div class="row cal-nojs__rowTitle"><h4>Speeches</h4></div>'
        + _panel("9:35 a.m.", "Speech - Governor", "27")
        + '<div class="row cal-nojs__rowTitle"><h4 class="col-md-12">FOMC Meetings </h4></div>'
        + "".join(panels)
        + '<div class="row cal-nojs__rowTitle"><h4>Beige Book</h4></div>'
        + _panel("2:00 p.m.", "Beige Book", "13")
    )


def _utc_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def test_calendar_meeting_time_uses_eastern_standard_time() -> None:
    page = _calendar(
        _panel("2:00 p.m.", "FOMC Meeting</p><p>Two-day meeting, January 26-27&nbsp;", "27"),
        _panel("2:00 p.m.", "FOMC Minutes</p><p>Meeting of December 15-16, 2020", "6"),
    )
    assert calendar_statement_ms(page, date(2021, 1, 27)) == _utc_ms(2021, 1, 27, 19, 0)


def test_calendar_meeting_time_uses_daylight_time() -> None:
    page = _calendar(_panel("2:00 p.m.", "FOMC Meeting", "16"))
    assert calendar_statement_ms(page, date(2021, 6, 16)) == _utc_ms(2021, 6, 16, 18, 0)


def test_other_sections_and_minutes_are_not_meetings() -> None:
    page = _calendar(_panel("2:00 p.m.", "FOMC Minutes", "27"))
    with pytest.raises(ValueError, match="정확히 하나"):
        calendar_statement_ms(page, date(2021, 1, 27))


def test_calendar_without_fomc_section_fails_closed() -> None:
    with pytest.raises(ValueError, match="FOMC Meetings"):
        calendar_statement_ms("<p>nothing</p>", date(2021, 1, 27))


def test_statement_release_time_and_zone_label_are_checked() -> None:
    page = (
        "<p>January 27, 2021</p><h3>Federal Reserve issues FOMC statement</h3>"
        "<p>For release at 2:00 p.m. EST</p>"
    )
    assert statement_release_ms(page, date(2021, 1, 27)) == _utc_ms(2021, 1, 27, 19, 0)
    with pytest.raises(ValueError, match="서머타임"):
        statement_release_ms(page.replace("EST", "EDT"), date(2021, 1, 27))
    with pytest.raises(ValueError, match="날짜"):
        statement_release_ms(page, date(2021, 1, 28))
    with pytest.raises(ValueError, match="형식"):
        statement_release_ms("<p>Minutes of the FOMC</p>", date(2021, 1, 27))


def test_snapshot_cutoff_is_eastern_midnight_of_statement_day() -> None:
    assert _cutoff(date(2021, 1, 27)) == "20210127050000"
    assert _cutoff(date(2021, 7, 28)) == "20210728040000"


def test_time_difference_is_a_discrepancy() -> None:
    row = AuditRow("2021-01", 200, 100, 150, "s", "h1", "v", "h2")
    assert row.status == "DISCREPANCY"


def test_statement_dates_cover_train_meetings() -> None:
    assert len(STATEMENT_DATES) == 16
    assert all(day.weekday() == 2 for day in STATEMENT_DATES)
    assert {day.year for day in STATEMENT_DATES} == {2021, 2022}
