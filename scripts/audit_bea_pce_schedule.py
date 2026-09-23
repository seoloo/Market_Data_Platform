"""BEA 월간 PCE의 이전 보도자료 예고와 실제 발표 시각을 대조한다.

공식 페이지를 읽고 후보 CSV를 표준 출력으로만 낸다. DB·파일은 쓰지 않으며
이 결과만으로 G6 coverage를 인증하지 않는다.
"""

from __future__ import annotations

import csv
import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import httpx

_EASTERN = ZoneInfo("America/New_York")
_DATE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([AP])\.?M\.?", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


@dataclass(frozen=True)
class Resolution:
    """웹 머리말과 다른 발표 시각을 다른 공식 원천으로 확정한 기록."""

    official_ms: int
    evidence: tuple[str, ...]


RESOLUTIONS: dict[str, Resolution] = {
    # 2021-11-24 10:00 EST. 웹 머리말만 08:30이고, 사전 일정표(2021-02-09 수정본)·
    # 직전 보도자료 예고·해당 발표의 공식 PDF가 모두 10:00이다.
    "2021-10": Resolution(
        official_ms=1_637_766_000_000,
        evidence=(
            "https://apps.bea.gov/scb/issues/2020/12-december/1220-news-releases-2021.htm",
            "https://www.bea.gov/news/2021/personal-income-and-outlays-september-2021",
            "https://www.bea.gov/sites/default/files/2021-11/pi1021.pdf",
        ),
    ),
}


@dataclass(frozen=True)
class BEARelease:
    period: str
    source_url: str
    release_ms: int
    next_release_ms: int


@dataclass(frozen=True)
class AuditRow:
    event_key: str
    scheduled_ms: int
    vintage_ms: int
    observed_ms: int
    source: str
    verification_url: str
    resolution: Resolution | None = None

    @property
    def status(self) -> str:
        """``RESOLVED``는 예고 시각이 기록된 공식 시각과 같을 때만 허용한다."""
        if self.scheduled_ms == self.observed_ms:
            return "MATCH"
        if self.resolution is not None and self.resolution.official_ms == self.scheduled_ms:
            return "RESOLVED"
        return "DISCREPANCY"


def _page_text(page: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", page))).strip()


def _et_ms(fragment: str) -> int:
    date = _DATE.search(fragment)
    time = _TIME.search(fragment)
    if date is None or time is None:
        raise ValueError(f"BEA 날짜·시각을 읽을 수 없다: {fragment[:100]}")
    month_name = date.group(1).lower()
    if month_name not in _MONTHS:
        raise ValueError(f"BEA 월 이름을 읽을 수 없다: {date.group(1)}")
    month = _MONTHS.index(month_name) + 1
    hour = int(time.group(1)) % 12 + (12 if time.group(3).upper() == "P" else 0)
    point = datetime(
        int(date.group(3)),
        month,
        int(date.group(2)),
        hour,
        int(time.group(2)),
        tzinfo=_EASTERN,
    )
    label = re.search(r"\b(EST|EDT)\b", fragment)
    if label is not None and label.group(1) != point.tzname():
        raise ValueError(f"BEA 동부시각 표기와 실제 DST가 다르다: {fragment[:100]}")
    return int(point.timestamp() * 1000)


def parse_release(page: str, *, period: str, source_url: str) -> BEARelease:
    text = _page_text(page)
    header = re.search(r"EMBARGOED (?:UNTIL RELEASE AT|FOR RELEASE:)\s+(.{0,110})", text, re.I)
    next_release = re.search(r"Next release:\s+(.{0,110})", text, re.I)
    if header is None or next_release is None:
        raise ValueError(f"BEA 발표/다음 발표 표기가 없다: {source_url}")
    release_ms = _et_ms(header.group(1))
    next_ms = _et_ms(next_release.group(1))
    if next_ms <= release_ms:
        raise ValueError(f"BEA 다음 발표가 현재 발표보다 빠르다: {source_url}")
    return BEARelease(period, source_url, release_ms, next_ms)


def _periods() -> tuple[tuple[int, int], ...]:
    return tuple(
        (year, month)
        for year in range(2020, 2023)
        for month in range(1, 13)
        if (year, month) >= (2020, 11) and (year, month) <= (2022, 11)
    )


def _url(year: int, month: int) -> str:
    release_year = year + 1 if month == 12 else year
    suffix = "-and-annual-update" if (year, month) == (2022, 8) else ""
    return (
        f"https://www.bea.gov/news/{release_year}/"
        f"personal-income-and-outlays-{_MONTHS[month - 1]}-{year}{suffix}"
    )


def audit_releases(
    releases: list[BEARelease],
    resolutions: dict[str, Resolution] | None = None,
) -> tuple[AuditRow, ...]:
    known = RESOLUTIONS if resolutions is None else resolutions
    return tuple(
        AuditRow(
            event_key=current.period,
            scheduled_ms=prior.next_release_ms,
            vintage_ms=prior.release_ms,
            observed_ms=current.release_ms,
            source=prior.source_url,
            verification_url=current.source_url,
            resolution=known.get(current.period),
        )
        for prior, current in pairwise(releases)
    )


def main() -> int:
    releases: list[BEARelease] = []
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for year, month in _periods():
            url = _url(year, month)
            response = client.get(url)
            response.raise_for_status()
            period = f"{year}-{month:02d}"
            releases.append(parse_release(response.text, period=period, source_url=url))
    rows = audit_releases(releases)
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(
        (
            "indicator",
            "event_key",
            "scheduled_ms",
            "vintage_ms",
            "observed_ms",
            "status",
            "source",
            "verification_url",
        )
    )
    for row in rows:
        writer.writerow(
            (
                "PCE",
                row.event_key,
                row.scheduled_ms,
                row.vintage_ms,
                row.observed_ms,
                row.status,
                row.source,
                row.verification_url,
            )
        )
    return 2 if any(row.status == "DISCREPANCY" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
