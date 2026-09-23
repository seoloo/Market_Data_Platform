"""BLS 로컬 보도자료 대조기의 형식·서머타임·누락 검사."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.audit_bls_schedule import (
    EXPECTED_RELEASE_DATES,
    BLSRelease,
    audit_releases,
    expected_urls,
    load_directory,
    parse_release,
)

CPI_DEC_2020 = (
    "<pre>Transmission of material in this release is embargoed until\n"
    "8:30 a.m. (ET) Thursday, December 10, 2020</pre>"
    "<p>The Consumer Price Index for December 2020 is scheduled to be released on\n"
    "Wednesday, January 13, 2021, at 8:30 a.m. (ET).</p>"
)
EMPSIT_MAY_2021 = (
    "<pre>Transmission of material in this release is embargoed until 8:30 a.m. (ET)"
    " Friday, June 4, 2021</pre>"
    "<p>The Employment Situation for June is scheduled to be released on Friday,"
    " July 2, 2021, at 8:30 a.m. (ET).</p>"
)


def _utc_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def test_cpi_release_announces_next_event_in_eastern_standard_time() -> None:
    release = parse_release(CPI_DEC_2020, source_file="cpi.htm", sha256="a")
    assert release.indicator == "CPI"
    assert release.release_ms == _utc_ms(2020, 12, 10, 13, 30)
    assert release.next_release_ms == _utc_ms(2021, 1, 13, 13, 30)
    assert release.next_period == "2020-12"


def test_employment_situation_uses_daylight_time_and_infers_period_year() -> None:
    release = parse_release(EMPSIT_MAY_2021, source_file="empsit.htm", sha256="b")
    assert release.indicator == "NFP"
    assert release.release_ms == _utc_ms(2021, 6, 4, 12, 30)
    assert release.next_release_ms == _utc_ms(2021, 7, 2, 12, 30)
    assert release.next_period == "2021-06"


def test_employment_release_number_between_embargo_and_time() -> None:
    page = (
        "Transmission of material in this news release is embargoed until USDL-21-0582 "
        "8:30 a.m. (ET) Friday, April 2, 2021 Technical information: ..."
        "The Employment Situation for April is scheduled to be released on Friday, "
        "May 7, 2021, at 8:30 a.m. (ET)."
    )
    release = parse_release(page, source_file="04022021.html", sha256="e")
    assert release.release_ms == _utc_ms(2021, 4, 2, 12, 30)
    assert release.next_release_ms == _utc_ms(2021, 5, 7, 12, 30)


def test_december_period_announced_in_january_belongs_to_previous_year() -> None:
    page = EMPSIT_MAY_2021.replace("June 4, 2021", "December 3, 2021").replace(
        "for June is scheduled to be released on Friday, July 2, 2021",
        "for December is scheduled to be released on Friday, January 7, 2022",
    )
    assert parse_release(page, source_file="x", sha256="c").next_period == "2021-12"


def test_page_without_schedule_sentence_fails_closed() -> None:
    with pytest.raises(ValueError, match="다음 발표"):
        parse_release("<p>Real Earnings - November 2020</p>", source_file="x", sha256="d")


def test_missing_or_changed_release_is_a_discrepancy() -> None:
    prior = BLSRelease("CPI", "a.htm", "sa", 100, 200, "2020-12")
    later = BLSRelease("CPI", "b.htm", "sb", 250, 300, "2021-01")
    rows = audit_releases([later, prior])
    assert [row.status for row in rows] == ["DISCREPANCY"]
    assert rows[0].source_sha256 == "sa"
    assert rows[0].verification_sha256 == "sb"


def test_series_are_paired_separately_and_duplicates_are_rejected() -> None:
    cpi = BLSRelease("CPI", "c1", "h1", 100, 200, "2020-12")
    cpi_next = BLSRelease("CPI", "c2", "h2", 200, 300, "2021-01")
    nfp = BLSRelease("NFP", "n1", "h3", 150, 250, "2020-12")
    nfp_next = BLSRelease("NFP", "n2", "h4", 250, 350, "2021-01")
    rows = audit_releases([nfp_next, cpi, nfp, cpi_next])
    assert [(row.indicator, row.status) for row in rows] == [("CPI", "MATCH"), ("NFP", "MATCH")]
    with pytest.raises(ValueError, match="중복"):
        audit_releases([cpi, cpi])


def test_directory_hashes_saved_files(tmp_path: Path) -> None:
    (tmp_path / "cpi_12102020.htm").write_text(CPI_DEC_2020, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    releases = load_directory(tmp_path)
    assert [release.source_file for release in releases] == ["cpi_12102020.htm"]
    assert len(releases[0].sha256) == 64


def test_expected_url_list_covers_train_events() -> None:
    urls = expected_urls()
    assert len(urls) == 50
    assert urls[0].endswith("/cpi_12102020.htm")
    assert urls[-1].endswith("/empsit_12022022.htm")
    for days in EXPECTED_RELEASE_DATES.values():
        in_train = [day for day in days if day.year in (2021, 2022)]
        assert len(in_train) == 24
