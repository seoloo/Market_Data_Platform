"""FOMC 성명 발표 시각을 회의 **전** 웹 아카이브 스냅샷과 실제 성명으로 대조한다.

연간 일정 발표문(연준 보도자료)은 회의 **날짜**만 알린다. 성명 **시각**은 연준 월별
캘린더에 표기되며, 회의 전에 찍힌 Internet Archive 스냅샷의 시각을 그 판본이
공개돼 있던 증거(`vintage_ms`)로 쓴다. 실제 발표 시각은 성명 보도자료의
``For release at``에서 읽는다. 각 원문의 SHA-256을 남긴다.

후보 CSV를 표준 출력으로만 내며 DB·파일은 쓰지 않고, 이 결과만으로 G6 coverage를
인증하지 않는다. G6의 FOMC는 원 설계(05-risk-engine G6)대로 **금리 결정 성명**이며
의사록은 포함하지 않는다.
"""

from __future__ import annotations

import csv
import hashlib
import html
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx

_EASTERN = ZoneInfo("America/New_York")
_CDX = "https://web.archive.org/cdx/search/cdx"
_FED = "https://www.federalreserve.gov"
_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?", re.I)
_PANEL = re.compile(
    r'<div class="col-xs-2">\s*<p>(.*?)</p>\s*</div>\s*'
    r'<div class="col-xs-7">(.*?)</div>\s*'
    r'<div class="col-xs-3">\s*<p>(.*?)</p>',
    re.S,
)
_RELEASE = re.compile(
    r"([A-Za-z]+ \d{1,2}, \d{4}) Federal Reserve issues FOMC statement "
    r"For release at (\d{1,2}:\d{2}\s*[ap]\.m\.) (EST|EDT)",
    re.I,
)
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

STATEMENT_DATES: tuple[date, ...] = (
    # 2021: monetary20200702a (2020-07-02 공개) · 2022: monetary20210604a (2021-06-04 공개).
    # 모두 화-수 회의이며 성명은 둘째 날이다.
    date(2021, 1, 27),
    date(2021, 3, 17),
    date(2021, 4, 28),
    date(2021, 6, 16),
    date(2021, 7, 28),
    date(2021, 9, 22),
    date(2021, 11, 3),
    date(2021, 12, 15),
    date(2022, 1, 26),
    date(2022, 3, 16),
    date(2022, 5, 4),
    date(2022, 6, 15),
    date(2022, 7, 27),
    date(2022, 9, 21),
    date(2022, 11, 2),
    date(2022, 12, 14),
)


@dataclass(frozen=True)
class AuditRow:
    event_key: str
    scheduled_ms: int
    vintage_ms: int
    observed_ms: int
    snapshot_url: str
    snapshot_sha256: str
    verification_url: str
    verification_sha256: str

    @property
    def status(self) -> str:
        return "MATCH" if self.scheduled_ms == self.observed_ms else "DISCREPANCY"


def _text(fragment: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _et_ms(day: date, clock: str) -> int:
    match = _CLOCK.search(clock)
    if match is None:
        raise ValueError(f"시각을 읽을 수 없다: {clock!r}")
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "p" else 0)
    point = datetime(day.year, day.month, day.day, hour, int(match.group(2)), tzinfo=_EASTERN)
    return int(point.timestamp() * 1000)


def calendar_statement_ms(page: str, day: date) -> int:
    """월별 캘린더의 FOMC 절에서 ``day``에 발표되는 회의 항목의 시각을 읽는다."""
    start = page.find("FOMC Meetings")
    if start < 0:
        raise ValueError("캘린더에 FOMC Meetings 절이 없다")
    end = page.find("cal-nojs__rowTitle", start)
    section = page[start : end if end > 0 else len(page)]
    matches = [
        _et_ms(day, _text(clock))
        for clock, body, days in _PANEL.findall(section)
        if "FOMC Meeting" in _text(body)
        and day.day in {int(value) for value in re.findall(r"\d+", _text(days))}
    ]
    if len(matches) != 1:
        raise ValueError(f"{day} FOMC 회의 항목이 정확히 하나가 아니다: {len(matches)}개")
    return matches[0]


def statement_release_ms(page: str, day: date) -> int:
    """성명 보도자료의 ``For release at``과 표기 시간대(EST/EDT)를 검증해 읽는다."""
    match = _RELEASE.search(_text(page))
    if match is None:
        raise ValueError(f"{day} 성명 보도자료 형식이 아니다")
    published = datetime.strptime(match.group(1), "%B %d, %Y").replace(tzinfo=UTC).date()
    if published != day:
        raise ValueError(f"성명 날짜가 다르다: {published} != {day}")
    observed = _et_ms(day, match.group(2))
    zone = datetime.fromtimestamp(observed / 1000, tz=_EASTERN).tzname()
    if zone != match.group(3).upper():
        raise ValueError(f"{day} 성명의 {match.group(3)} 표기가 실제 서머타임과 다르다")
    return observed


def _calendar_url(day: date) -> str:
    return f"{_FED}/newsevents/{day.year}-{_MONTHS[day.month - 1]}.htm"


def _statement_url(day: date) -> str:
    return f"{_FED}/newsevents/pressreleases/monetary{day:%Y%m%d}a.htm"


def _cutoff(day: date) -> str:
    """성명일 동부시각 0시(UTC 표기). 이보다 늦은 스냅샷은 판본으로 쓰지 않는다."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=_EASTERN).astimezone(UTC)
    return midnight.strftime("%Y%m%d%H%M%S")


def _snapshot_ms(timestamp: str) -> int:
    parsed = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _with_retry(action: Callable[[], httpx.Response], attempts: int = 4) -> httpx.Response:
    for attempt in range(attempts):
        try:
            response = action()
            response.raise_for_status()
            return response
        except httpx.HTTPError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("도달 불가")  # pragma: no cover


def audit_meeting(client: httpx.Client, day: date) -> AuditRow:
    calendar = _calendar_url(day)
    listing = _with_retry(
        lambda: client.get(
            _CDX,
            params={
                "url": calendar,
                "to": _cutoff(day),
                "filter": "statuscode:200",
                "output": "json",
                "fl": "timestamp",
            },
        )
    )
    rows = listing.json()[1:] if listing.text.strip() else []
    if not rows:
        raise ValueError(f"{day} 이전 캘린더 스냅샷이 없다")
    stamp = rows[-1][0]
    snapshot_url = f"https://web.archive.org/web/{stamp}id_/{calendar}"
    snapshot = _with_retry(lambda: client.get(snapshot_url))
    statement_url = _statement_url(day)
    statement = _with_retry(lambda: client.get(statement_url))
    return AuditRow(
        event_key=f"{day:%Y-%m}",
        scheduled_ms=calendar_statement_ms(snapshot.text, day),
        vintage_ms=_snapshot_ms(stamp),
        observed_ms=statement_release_ms(statement.text, day),
        snapshot_url=snapshot_url,
        snapshot_sha256=hashlib.sha256(snapshot.content).hexdigest(),
        verification_url=statement_url,
        verification_sha256=hashlib.sha256(statement.content).hexdigest(),
    )


def main() -> int:
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(
        (
            "indicator",
            "event_key",
            "scheduled_ms",
            "vintage_ms",
            "observed_ms",
            "status",
            "snapshot_url",
            "snapshot_sha256",
            "verification_url",
            "verification_sha256",
        )
    )
    failed = False
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for day in STATEMENT_DATES:
            row = audit_meeting(client, day)
            failed = failed or row.status == "DISCREPANCY"
            writer.writerow(
                (
                    "FOMC",
                    row.event_key,
                    row.scheduled_ms,
                    row.vintage_ms,
                    row.observed_ms,
                    row.status,
                    row.snapshot_url,
                    row.snapshot_sha256,
                    row.verification_url,
                    row.verification_sha256,
                )
            )
            sys.stdout.flush()
            time.sleep(1)  # 공개 아카이브에 부담을 주지 않는다.
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
