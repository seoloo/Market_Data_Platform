"""BLS CPI·고용보고서의 이전 보도자료 예고와 실제 발표 시각을 로컬 파일로 대조한다.

BLS는 이용 정책에 맞지 않는 자동 수집을 금지하고 스크립트 요청에 403을 반환한다
(2026-09-23 확인). 그래서 이 도구는 **네트워크에 접근하지 않고**, 사용자가
브라우저로 저장한 보도자료 HTML만 읽는다. 각 파일의 SHA-256을 남겨 근거를
재현할 수 있게 한다. 후보 CSV를 표준 출력으로만 내며 DB·파일은 쓰지 않고,
이 결과만으로 G6 coverage를 인증하지 않는다.

사용 예::

    python scripts/audit_bls_schedule.py --print-urls
    python scripts/audit_bls_schedule.py data/macro_sources/bls
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")
_ARCHIVE = "https://www.bls.gov/news.release/archives"
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_WEEKDAY = r"(?:[A-Za-z]+,\s+)?"
_DATE = r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})"
_CLOCK = r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?"
_EMBARGO = re.compile(
    rf"embargoed until\s+{_CLOCK}\s*\(ET\),?\s*{_WEEKDAY}{_DATE}",
    re.I,
)
_NEXT = re.compile(
    rf"The (Consumer Price Index|Employment Situation) for ([A-Za-z]+)(?:\s+\d{{4}})?"
    rf"\s+is scheduled to be released on\s+{_WEEKDAY}{_DATE},?\s+at\s+{_CLOCK}\s*\(ET\)",
    re.I,
)
_INDICATORS = {"consumer price index": "CPI", "employment situation": "NFP"}
_FILE_PREFIX = {"CPI": "cpi", "NFP": "empsit"}
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

EXPECTED_RELEASE_DATES: dict[str, tuple[date, ...]] = {
    # TRAIN(2021-01-03~2022-12-31) 발표 24건 + 첫 예고를 담은 직전 발표 1건.
    # 저장할 파일을 안내하는 목록일 뿐이며, 근거는 저장된 파일 자체다.
    "CPI": (
        date(2020, 12, 10),
        date(2021, 1, 13),
        date(2021, 2, 10),
        date(2021, 3, 10),
        date(2021, 4, 13),
        date(2021, 5, 12),
        date(2021, 6, 10),
        date(2021, 7, 13),
        date(2021, 8, 11),
        date(2021, 9, 14),
        date(2021, 10, 13),
        date(2021, 11, 10),
        date(2021, 12, 10),
        date(2022, 1, 12),
        date(2022, 2, 10),
        date(2022, 3, 10),
        date(2022, 4, 12),
        date(2022, 5, 11),
        date(2022, 6, 10),
        date(2022, 7, 13),
        date(2022, 8, 10),
        date(2022, 9, 13),
        date(2022, 10, 13),
        date(2022, 11, 10),
        date(2022, 12, 13),
    ),
    "NFP": (
        date(2020, 12, 4),
        date(2021, 1, 8),
        date(2021, 2, 5),
        date(2021, 3, 5),
        date(2021, 4, 2),
        date(2021, 5, 7),
        date(2021, 6, 4),
        date(2021, 7, 2),
        date(2021, 8, 6),
        date(2021, 9, 3),
        date(2021, 10, 8),
        date(2021, 11, 5),
        date(2021, 12, 3),
        date(2022, 1, 7),
        date(2022, 2, 4),
        date(2022, 3, 4),
        date(2022, 4, 1),
        date(2022, 5, 6),
        date(2022, 6, 3),
        date(2022, 7, 8),
        date(2022, 8, 5),
        date(2022, 9, 2),
        date(2022, 10, 7),
        date(2022, 11, 4),
        date(2022, 12, 2),
    ),
}


@dataclass(frozen=True)
class BLSRelease:
    indicator: str
    source_file: str
    sha256: str
    release_ms: int
    next_release_ms: int
    next_period: str


@dataclass(frozen=True)
class AuditRow:
    indicator: str
    event_key: str
    scheduled_ms: int
    vintage_ms: int
    observed_ms: int
    source_file: str
    source_sha256: str
    verification_file: str
    verification_sha256: str

    @property
    def status(self) -> str:
        return "MATCH" if self.scheduled_ms == self.observed_ms else "DISCREPANCY"


def _page_text(page: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", page))).strip()


def _month(name: str) -> int:
    lowered = name.lower()
    if lowered not in _MONTHS:
        raise ValueError(f"BLS 월 이름을 읽을 수 없다: {name}")
    return _MONTHS.index(lowered) + 1


def _et_ms(month_name: str, day: str, year: str, hour: str, minute: str, meridiem: str) -> int:
    """BLS의 ``(ET)``는 EST/EDT를 밝히지 않으므로 날짜의 서머타임으로 변환한다."""
    point = datetime(
        int(year),
        _month(month_name),
        int(day),
        int(hour) % 12 + (12 if meridiem.lower() == "p" else 0),
        int(minute),
        tzinfo=_EASTERN,
    )
    return int(point.timestamp() * 1000)


def parse_release(page: str, *, source_file: str, sha256: str) -> BLSRelease:
    text = _page_text(page)
    embargo = _EMBARGO.search(text)
    upcoming = _NEXT.search(text)
    if embargo is None or upcoming is None:
        raise ValueError(f"BLS 발표/다음 발표 표기가 없다: {source_file}")
    hour, minute, meridiem, month_name, day, year = embargo.groups()
    release_ms = _et_ms(month_name, day, year, hour, minute, meridiem)
    subject, period_month, n_month, n_day, n_year, n_hour, n_minute, n_meridiem = upcoming.groups()
    next_ms = _et_ms(n_month, n_day, n_year, n_hour, n_minute, n_meridiem)
    if next_ms <= release_ms:
        raise ValueError(f"BLS 다음 발표가 현재 발표보다 빠르다: {source_file}")
    reference_month = _month(period_month)
    scheduled_month = _month(n_month)
    reference_year = int(n_year) if reference_month <= scheduled_month else int(n_year) - 1
    return BLSRelease(
        indicator=_INDICATORS[subject.lower()],
        source_file=source_file,
        sha256=sha256,
        release_ms=release_ms,
        next_release_ms=next_ms,
        next_period=f"{reference_year}-{reference_month:02d}",
    )


def audit_releases(releases: list[BLSRelease]) -> tuple[AuditRow, ...]:
    """종류별로 발표 시각 순서로 정렬해 인접한 예고와 실제 발표를 대조한다."""
    rows: list[AuditRow] = []
    for indicator in sorted({release.indicator for release in releases}):
        series = sorted(
            (release for release in releases if release.indicator == indicator),
            key=lambda release: release.release_ms,
        )
        times = [release.release_ms for release in series]
        if len(times) != len(set(times)):
            raise ValueError(f"{indicator} 같은 발표 파일이 중복됐다")
        rows.extend(
            AuditRow(
                indicator=indicator,
                event_key=prior.next_period,
                scheduled_ms=prior.next_release_ms,
                vintage_ms=prior.release_ms,
                observed_ms=current.release_ms,
                source_file=prior.source_file,
                source_sha256=prior.sha256,
                verification_file=current.source_file,
                verification_sha256=current.sha256,
            )
            for prior, current in pairwise(series)
        )
    return tuple(rows)


def expected_urls() -> tuple[str, ...]:
    return tuple(
        f"{_ARCHIVE}/{_FILE_PREFIX[indicator]}_{day:%m%d%Y}.htm"
        for indicator, days in EXPECTED_RELEASE_DATES.items()
        for day in days
    )


def load_directory(directory: Path) -> list[BLSRelease]:
    files = sorted(path for path in directory.iterdir() if path.suffix.lower() in (".htm", ".html"))
    if not files:
        raise ValueError(f"저장된 BLS HTML이 없다: {directory}")
    releases: list[BLSRelease] = []
    for path in files:
        raw = path.read_bytes()
        releases.append(
            parse_release(
                raw.decode("utf-8", errors="replace"),
                source_file=path.name,
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    return releases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, help="저장한 BLS 보도자료 폴더")
    parser.add_argument("--print-urls", action="store_true", help="저장할 보도자료 URL 목록")
    args = parser.parse_args()
    if args.print_urls:
        for url in expected_urls():
            print(url)
        return 0
    if args.directory is None:
        parser.error("폴더 또는 --print-urls 가 필요합니다")

    rows = audit_releases(load_directory(args.directory))
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(
        (
            "indicator",
            "event_key",
            "scheduled_ms",
            "vintage_ms",
            "observed_ms",
            "status",
            "source_file",
            "source_sha256",
            "verification_file",
            "verification_sha256",
        )
    )
    for row in rows:
        writer.writerow(
            (
                row.indicator,
                row.event_key,
                row.scheduled_ms,
                row.vintage_ms,
                row.observed_ms,
                row.status,
                row.source_file,
                row.source_sha256,
                row.verification_file,
                row.verification_sha256,
            )
        )
    return 2 if any(row.status == "DISCREPANCY" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
