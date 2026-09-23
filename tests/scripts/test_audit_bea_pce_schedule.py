"""BEA 사전 일정 대조기의 시간·서머타임·형식 실패 검사."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from scripts.audit_bea_pce_schedule import (
    RESOLUTIONS,
    BEARelease,
    Resolution,
    _et_ms,
    _url,
    audit_releases,
    parse_release,
)


def test_previous_release_announces_next_event() -> None:
    page = (
        "<div>EMBARGOED UNTIL RELEASE AT 8:30 A.M. EST, WEDNESDAY, DECEMBER 23, 2020</div>"
        "<p>Next release:&nbsp;January 29, 2021, at 8:30 A.M. EST<br />"
        "Personal Income and Outlays, December 2020</p>"
    )
    release = parse_release(page, period="2020-11", source_url="https://www.bea.gov/example")
    assert release.release_ms == int(datetime(2020, 12, 23, 13, 30, tzinfo=UTC).timestamp() * 1000)
    assert release.next_release_ms == int(
        datetime(2021, 1, 29, 13, 30, tzinfo=UTC).timestamp() * 1000
    )


def test_dst_changes_utc_hour() -> None:
    assert _et_ms("May 28, 2021 at 8:30 A.M. EDT") == int(
        datetime(2021, 5, 28, 12, 30, tzinfo=UTC).timestamp() * 1000
    )
    with pytest.raises(ValueError, match="DST"):
        _et_ms("May 28, 2021 at 8:30 A.M. EST")


def test_unknown_month_name_fails_closed() -> None:
    with pytest.raises(ValueError, match="월 이름"):
        _et_ms("Mai 28, 2021 at 8:30 A.M. EDT")


def test_release_year_differs_from_reference_period_at_year_end() -> None:
    assert (
        _url(2020, 12) == "https://www.bea.gov/news/2021/personal-income-and-outlays-december-2020"
    )


def test_missing_next_release_fails_closed() -> None:
    with pytest.raises(ValueError, match="다음 발표"):
        parse_release(
            "EMBARGOED UNTIL RELEASE AT 8:30 A.M. EST, DECEMBER 23, 2020",
            period="2020-11",
            source_url="test",
        )


def test_changed_or_inconsistent_release_time_is_not_silently_accepted() -> None:
    prior = BEARelease("2021-09", "prior", 100, 200)
    current = BEARelease("2021-10", "current", 150, 300)
    rows = audit_releases([prior, current])
    assert len(rows) == 1
    assert rows[0].status == "DISCREPANCY"
    assert rows[0].scheduled_ms == 200
    assert rows[0].observed_ms == 150


def test_documented_web_header_error_is_resolved_only_at_official_time() -> None:
    prior = BEARelease("2021-09", "prior", 100, 200)
    current = BEARelease("2021-10", "current", 150, 300)
    evidence = ("schedule", "prior", "pdf")
    resolved = audit_releases([prior, current], {"2021-10": Resolution(200, evidence)})
    assert resolved[0].status == "RESOLVED"
    stale = audit_releases([prior, current], {"2021-10": Resolution(999, evidence)})
    assert stale[0].status == "DISCREPANCY"


def test_known_bea_2021_11_24_release_is_ten_am_eastern() -> None:
    resolution = RESOLUTIONS["2021-10"]
    assert resolution.official_ms == _et_ms("November 24, 2021 at 10:00 A.M. EST")
    assert len(resolution.evidence) == 3
