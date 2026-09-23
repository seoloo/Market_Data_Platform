"""FOMC 후 Powell 기자회견 시각을 사전 웹 아카이브 스냅샷으로 확인한다.

G6의 POWELL은 원 설계(05-risk-engine G6 "Powell 기자회견")와 2026-09-23 사용자 결정에
따라 **FOMC 기자회견만**이다. 연준 월별 캘린더는 기자회견을 시각 없이 표기하므로,
연준 홈페이지 배너 ``Watch Chair Powell's press conference live on <날짜> at <시각>``의
Internet Archive 스냅샷을 판본으로 쓴다. 스냅샷은 POWELL 블랙아웃 시작
(예정 시각 − ``before_min``)보다 앞서야 한다. 기자회견의 실제 시작 시각을 적은
공식 기록은 찾지 못했으므로 ``status``는 예고 확인(``ANNOUNCED``)까지만 판정한다.
판본이 없는 날은 사용자가 승인한 날(``APPROVED_GAP_DAYS``)만 ``GAP``이며, 그 동부시각
하루를 달력 공백(``CoverageGap``)으로 낼 구간을 함께 출력한다. 그 밖의 미발견은 실패다.

후보 CSV를 표준 출력으로만 내며 DB·파일은 쓰지 않고, coverage를 인증하지 않는다.
"""

from __future__ import annotations

import csv
import hashlib
import html
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from scripts.audit_fomc_schedule import STATEMENT_DATES, _snapshot_ms, _with_retry

from auto_trading.config.loader import load_config

_EASTERN = ZoneInfo("America/New_York")
_CDX = "https://web.archive.org/cdx/search/cdx"
_HOME = "https://www.federalreserve.gov/"
_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_DAY = r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?"
_TIME = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[ap])\.?\s*m\.?"
_BANNERS = (
    # 2021-2022 실제 문구: "live on March 17 at 2:30 p.m." / "live on March 16, 2022 at 2:30 p.m."
    re.compile(rf"press conference live on\s+{_DAY}\s+at\s+{_TIME}", re.I),
    # 2022 하반기: "live at 2:30 p.m. on September 21, 2022"
    re.compile(rf"press conference live at\s+{_TIME}\s+on\s+{_DAY}", re.I),
)
_LOOKBACK = timedelta(days=14)
_MAX_SNAPSHOTS = 8

APPROVED_GAP_DAYS: frozenset[date] = frozenset(
    # 2026-09-23 사용자 결정 (1): 블랙아웃 전 시각 판본을 찾지 못한 기자회견일은
    # 관행(14:30)으로 채우지 않고 동부시각 하루 전체를 달력 사용 불가로 둔다.
    {date(2021, 1, 27), date(2021, 6, 16), date(2022, 6, 15)}
)


@dataclass(frozen=True)
class Announcement:
    scheduled_ms: int
    vintage_ms: int
    snapshot_url: str
    snapshot_sha256: str


def banner_time_ms(page: str, day: date) -> int | None:
    """홈페이지 배너가 ``day``의 기자회견 시각을 알리면 그 시각(UTC ms)을 돌려준다."""
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", page)))
    for pattern in _BANNERS:
        for match in pattern.finditer(text):
            # 연도가 없는 배너는 회의 연도로 읽는다. 월·일이 다르면 다른 회의다.
            year = match.group("year") or str(day.year)
            label = f"{match.group('month')} {match.group('day')}, {year}"
            try:
                announced = datetime.strptime(label, "%B %d, %Y").replace(tzinfo=UTC).date()
            except ValueError:
                continue
            if announced != day:
                continue
            hour = int(match.group("hour")) % 12 + (12 if match.group("ampm").lower() == "p" else 0)
            point = datetime(
                day.year, day.month, day.day, hour, int(match.group("minute")), tzinfo=_EASTERN
            )
            return int(point.timestamp() * 1000)
    return None


def unknown_day_gap(day: date) -> tuple[int, int]:
    """시각을 모르는 기자회견일의 공백 구간: 동부시각 그날 00:00 ~ 23:59:59.999."""
    start = datetime(day.year, day.month, day.day, tzinfo=_EASTERN)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=_EASTERN)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1


def is_before_blackout(announcement: Announcement, before_min: int) -> bool:
    """판본이 블랙아웃 시작보다 앞서야 그 창을 알고 있었다고 할 수 있다."""
    return announcement.vintage_ms <= announcement.scheduled_ms - before_min * 60_000


def find_announcement(client: httpx.Client, day: date, before_min: int) -> Announcement | None:
    # 표준 기자회견(14:30)의 블랙아웃 시작까지를 조회 상한으로 쓰고, 읽은 시각으로 다시 검증한다.
    latest = datetime(day.year, day.month, day.day, 14, 30, tzinfo=_EASTERN) - timedelta(
        minutes=before_min
    )
    earliest = latest - _LOOKBACK
    listing = _with_retry(
        lambda: client.get(
            _CDX,
            params={
                "url": "www.federalreserve.gov/",
                "from": earliest.astimezone(UTC).strftime("%Y%m%d%H%M%S"),
                "to": latest.astimezone(UTC).strftime("%Y%m%d%H%M%S"),
                "filter": "statuscode:200",
                "collapse": "digest",
                "output": "json",
                "fl": "timestamp",
            },
        )
    )
    stamps = [row[0] for row in (listing.json()[1:] if listing.text.strip() else [])]
    for stamp in reversed(stamps[-_MAX_SNAPSHOTS:]):
        url = f"https://web.archive.org/web/{stamp}id_/{_HOME}"

        def fetch(target: str = url) -> httpx.Response:
            return client.get(target)

        snapshot = _with_retry(fetch)
        scheduled = banner_time_ms(snapshot.text, day)
        if scheduled is not None:
            return Announcement(
                scheduled_ms=scheduled,
                vintage_ms=_snapshot_ms(stamp),
                snapshot_url=url,
                snapshot_sha256=hashlib.sha256(snapshot.content).hexdigest(),
            )
        time.sleep(1)
    return None


def main() -> int:
    before_min = load_config("backtest", apply_env=False).config.risk.blackout["POWELL"].before_min
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(
        (
            "indicator",
            "event_key",
            "scheduled_ms",
            "vintage_ms",
            "status",
            "snapshot_url",
            "sha256",
            "gap_from_ms",
            "gap_to_ms",
        )
    )
    failed = False
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for day in STATEMENT_DATES:
            found = find_announcement(client, day, before_min)
            if found is None:
                # 승인된 공백일만 GAP이다. 그 밖의 미발견은 일시 장애일 수 있으므로 실패다.
                approved = day in APPROVED_GAP_DAYS
                failed = failed or not approved
                gap_from, gap_to = unknown_day_gap(day)
                writer.writerow(
                    (
                        "POWELL",
                        f"{day:%Y-%m}",
                        "",
                        "",
                        "GAP" if approved else "NOT_FOUND",
                        "",
                        "",
                        gap_from if approved else "",
                        gap_to if approved else "",
                    )
                )
            else:
                ok = is_before_blackout(found, before_min)
                failed = failed or not ok
                writer.writerow(
                    (
                        "POWELL",
                        f"{day:%Y-%m}",
                        found.scheduled_ms,
                        found.vintage_ms,
                        "ANNOUNCED" if ok else "TOO_LATE",
                        found.snapshot_url,
                        found.snapshot_sha256,
                        "",
                        "",
                    )
                )
            sys.stdout.flush()
            time.sleep(1)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
