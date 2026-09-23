"""Powell 기자회견 배너 파서와 블랙아웃 이전 판본 조건 검사."""

from __future__ import annotations

from datetime import UTC, date, datetime

from scripts.audit_powell_press_schedule import (
    APPROVED_GAP_DAYS,
    Announcement,
    banner_time_ms,
    is_before_blackout,
    unknown_day_gap,
)

BANNER = (
    "<h3>FOMC Press Conference</h3><p>Watch Chair Powell's press conference live on&nbsp;"
    "March 16, 2022 at 2:30 p.m.</p>"
)


def _utc_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def test_banner_time_uses_daylight_time() -> None:
    assert banner_time_ms(BANNER, date(2022, 3, 16)) == _utc_ms(2022, 3, 16, 18, 30)


def test_banner_time_uses_standard_time() -> None:
    page = BANNER.replace("March 16, 2022", "January 26, 2022")
    assert banner_time_ms(page, date(2022, 1, 26)) == _utc_ms(2022, 1, 26, 19, 30)


def test_banner_without_year_uses_meeting_year() -> None:
    page = "Watch Chair Powell's press conference live on March 17 at 2:30 p.m. &nbsp;"
    assert banner_time_ms(page, date(2021, 3, 17)) == _utc_ms(2021, 3, 17, 18, 30)


def test_banner_with_time_before_date() -> None:
    page = "Watch Chair Powell's press conference live at 2:30 p.m. on September 21, 2022"
    assert banner_time_ms(page, date(2022, 9, 21)) == _utc_ms(2022, 9, 21, 18, 30)


def test_banner_for_another_meeting_or_on_demand_video_is_ignored() -> None:
    assert banner_time_ms(BANNER, date(2022, 5, 4)) is None
    on_demand = "Watch on-demand video from Chair Powell's press conference on December 16, 2020"
    assert banner_time_ms(on_demand, date(2020, 12, 16)) is None


def test_vintage_must_precede_blackout_start() -> None:
    scheduled = _utc_ms(2022, 3, 16, 18, 30)
    early = Announcement(scheduled, _utc_ms(2022, 3, 16, 18, 14), "u", "h")
    boundary = Announcement(scheduled, _utc_ms(2022, 3, 16, 18, 15), "u", "h")
    late = Announcement(scheduled, _utc_ms(2022, 3, 16, 18, 16), "u", "h")
    assert is_before_blackout(early, 15)
    assert is_before_blackout(boundary, 15)
    assert not is_before_blackout(late, 15)


def test_unknown_day_gap_covers_whole_eastern_day() -> None:
    assert unknown_day_gap(date(2021, 1, 27)) == (
        _utc_ms(2021, 1, 27, 5, 0),
        _utc_ms(2021, 1, 28, 5, 0) - 1,
    )
    assert unknown_day_gap(date(2022, 6, 15)) == (
        _utc_ms(2022, 6, 15, 4, 0),
        _utc_ms(2022, 6, 16, 4, 0) - 1,
    )


def test_approved_gap_days_are_the_three_unannounced_press_conferences() -> None:
    assert date(2021, 1, 27) in APPROVED_GAP_DAYS
    assert date(2021, 6, 16) in APPROVED_GAP_DAYS
    assert date(2022, 6, 15) in APPROVED_GAP_DAYS
    assert len(APPROVED_GAP_DAYS) == 3
