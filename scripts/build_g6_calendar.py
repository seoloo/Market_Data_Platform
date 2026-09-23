"""감사 CSV를 G6 달력 매니페스트로 묶고, ``macro_events`` 적재를 dry-run 한다.

사용 예::

    python scripts/build_g6_calendar.py build
    python scripts/build_g6_calendar.py load            # dry-run (DB 읽기 전용)
    python scripts/build_g6_calendar.py load --apply    # DB 쓰기. 사용자 승인 후에만

``build``는 네트워크에 접근하지 않고 ``data/macro_sources/audits/``의 감사 결과만 읽는다.
각 항목이 해당 종류의 블랙아웃 시작 전에 공개돼 있었는지(``vintage``), 월간 지표의
연속성, FOMC·기자회견 회의 목록 일치를 검사한 뒤 매니페스트를 쓴다.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.audit_fomc_schedule import STATEMENT_DATES

from auto_trading.backtest.calendar import CoverageGap
from auto_trading.backtest.calendar_manifest import CalendarEntry, CalendarManifest
from auto_trading.config.loader import load_config
from auto_trading.config.schema import BlackoutWindow
from auto_trading.storage.db import Database

NAME = "a1_train_g6_2021_2022"
# 02 사전등록 §11의 TRAIN 구간 (2021-01-03 00:00 ~ 2022-12-31 23:59:59.999 UTC).
FROM_MS = int(datetime(2021, 1, 3, tzinfo=UTC).timestamp() * 1000)
TO_MS = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1
AUDITS = Path("data/macro_sources/audits")
MANIFEST = Path("config/macro_calendar/a1_train_g6_2021_2022.json")
_EASTERN = ZoneInfo("America/New_York")
_BLS_ARCHIVE = "https://www.bls.gov/news.release/archives"
_MONTHLY = ("CPI", "NFP", "PCE")
_ALLOWED = {
    "pce.csv": {"MATCH", "RESOLVED"},
    "bls.csv": {"MATCH"},
    "fomc.csv": {"MATCH"},
    "powell.csv": {"ANNOUNCED", "GAP"},
}
_GAP_REASON = (
    "기자회견 시각의 블랙아웃 전 공개 판본 없음 (2026-09-23 사용자 결정: 하루 fail-closed)"
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    bad = sorted({row["status"] for row in rows} - _ALLOWED[path.name])
    if bad:
        raise ValueError(f"{path.name}에 허용되지 않은 감사 상태가 있다: {bad}")
    return rows


def _entry(
    indicator: str,
    row: dict[str, str],
    *,
    source: str,
    sha: str,
    released: str | None,
) -> CalendarEntry:
    return CalendarEntry(
        indicator=indicator,
        event_key=row["event_key"],
        scheduled_ms=int(row["scheduled_ms"]),
        vintage_ms=int(row["vintage_ms"]),
        released_ms=int(released) if released else None,
        source=source,
        source_sha256=sha,
    )


def collect(directory: Path) -> tuple[list[CalendarEntry], list[CoverageGap]]:
    """감사 CSV 4개를 항목과 공백으로 바꾼다. 원천 URL·해시를 그대로 옮긴다."""
    entries: list[CalendarEntry] = []
    gaps: list[CoverageGap] = []
    for row in _rows(directory / "pce.csv"):
        entries.append(
            _entry(
                "PCE",
                row,
                source=row["source"],
                sha=row["source_sha256"],
                released=row["observed_ms"],
            )
        )
    for row in _rows(directory / "bls.csv"):
        entries.append(
            _entry(
                row["indicator"],
                row,
                source=f"{_BLS_ARCHIVE}/{row['source_file']}",
                sha=row["source_sha256"],
                released=row["observed_ms"],
            )
        )
    for row in _rows(directory / "fomc.csv"):
        entries.append(
            _entry(
                "FOMC",
                row,
                source=row["snapshot_url"],
                sha=row["snapshot_sha256"],
                released=row["observed_ms"],
            )
        )
    for row in _rows(directory / "powell.csv"):
        if row["status"] == "GAP":
            gaps.append(
                CoverageGap(
                    from_ms=int(row["gap_from_ms"]),
                    to_ms=int(row["gap_to_ms"]),
                    indicators=frozenset({"POWELL"}),
                    reason=_GAP_REASON,
                )
            )
        else:
            entries.append(
                _entry("POWELL", row, source=row["snapshot_url"], sha=row["sha256"], released=None)
            )
    return entries, gaps


def _next_month(key: str) -> str:
    year, month = (int(part) for part in key.split("-"))
    return f"{year + month // 12}-{month % 12 + 1:02d}"


def validate(
    entries: Iterable[CalendarEntry],
    gaps: Iterable[CoverageGap],
    windows: dict[str, BlackoutWindow],
) -> dict[str, int]:
    """매니페스트 자체 검증 밖의 감사 규칙. 종류별 범위 내 항목 수를 돌려준다."""
    items = list(entries)
    for entry in items:
        start = entry.scheduled_ms - windows[entry.indicator].before_min * 60_000
        if entry.vintage_ms > start:
            raise ValueError(f"{entry.indicator} {entry.event_key} 판본이 블랙아웃 시작 뒤다")

    counts: dict[str, int] = {}
    for indicator in windows:
        inside = sorted(
            (e for e in items if e.indicator == indicator and FROM_MS <= e.scheduled_ms <= TO_MS),
            key=lambda e: e.scheduled_ms,
        )
        counts[indicator] = len(inside)
        if indicator in _MONTHLY:
            keys = [entry.event_key for entry in inside]
            if any(_next_month(a) != b for a, b in pairwise(keys)):
                raise ValueError(f"{indicator} 월간 발표에 빠진 달이 있다: {keys}")

    meetings = {f"{day:%Y-%m}" for day in STATEMENT_DATES}
    fomc = {e.event_key for e in items if e.indicator == "FOMC"}
    if fomc != meetings:
        raise ValueError("FOMC 항목이 연준 회의 목록과 다르다")
    # 공백은 동부시각 하루이므로 시작 시각을 동부시각으로 읽어야 성명일의 달이 된다.
    gap_months = {
        datetime.fromtimestamp(gap.from_ms / 1000, tz=_EASTERN).strftime("%Y-%m") for gap in gaps
    }
    powell = {e.event_key for e in items if e.indicator == "POWELL"}
    if powell & gap_months or powell | gap_months != meetings:
        raise ValueError("기자회견 항목과 공백의 합이 FOMC 회의 목록과 다르다")
    return counts


def build(directory: Path, out: Path) -> CalendarManifest:
    windows = dict(load_config("backtest", apply_env=False).config.risk.blackout)
    entries, gaps = collect(directory)
    counts = validate(entries, gaps, windows)
    manifest = CalendarManifest(
        name=NAME,
        from_ms=FROM_MS,
        to_ms=TO_MS,
        indicators=frozenset(windows),
        gaps=tuple(gaps),
        entries=tuple(entries),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=1, sort_keys=True)
    out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"manifest={out} digest={manifest.digest()}")
    print(f"TRAIN 범위 항목 수 {counts}, 공백 {len(gaps)}건")
    return manifest


def plan_load(
    manifest: CalendarManifest, conn: sqlite3.Connection
) -> tuple[list[CalendarEntry], int, list[str]]:
    """(새로 넣을 항목, 이미 동일한 항목 수, 충돌 목록). DB를 바꾸지 않는다."""
    fresh: list[CalendarEntry] = []
    same = 0
    conflicts: list[str] = []
    for entry in manifest.entries:
        row = conn.execute(
            "SELECT scheduled_ms, released_ms, source FROM macro_events"
            " WHERE indicator = ? AND period = ? AND vintage_ms = ?",
            (entry.indicator, entry.event_key, entry.vintage_ms),
        ).fetchone()
        if row is None:
            fresh.append(entry)
        elif tuple(row) == (entry.scheduled_ms, entry.released_ms, entry.db_source):
            same += 1
        else:
            conflicts.append(f"{entry.indicator} {entry.event_key}")
    return fresh, same, conflicts


def load(manifest_path: Path, db_path: str, *, apply: bool) -> int:
    manifest = CalendarManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
    with Database(db_path, read_only=not apply) as database:
        fresh, same, conflicts = plan_load(manifest, database.connection)
        by_kind: dict[str, int] = {}
        for entry in fresh:
            by_kind[entry.indicator] = by_kind.get(entry.indicator, 0) + 1
        mode = "APPLY" if apply else "DRY-RUN"
        print(f"[{mode}] db={db_path} digest={manifest.digest()}")
        print(f"  추가 예정 {len(fresh)}건 {dict(sorted(by_kind.items()))}")
        print(f"  이미 동일 {same}건, 충돌 {len(conflicts)}건 {conflicts}")
        if conflicts:
            return 2
        if not apply:
            return 0
        with database.transaction() as conn:
            conn.executemany(
                "INSERT INTO macro_events"
                " (indicator, scheduled_ms, released_ms, period, source, vintage_ms)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        e.indicator,
                        e.scheduled_ms,
                        e.released_ms,
                        e.event_key,
                        e.db_source,
                        e.vintage_ms,
                    )
                    for e in fresh
                ],
            )
        print(f"  {len(fresh)}건 적재 완료")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_cmd = sub.add_parser("build", help="감사 CSV → 매니페스트")
    build_cmd.add_argument("--audits", type=Path, default=AUDITS)
    build_cmd.add_argument("--out", type=Path, default=MANIFEST)
    load_cmd = sub.add_parser("load", help="매니페스트 → macro_events (기본 dry-run)")
    load_cmd.add_argument("--manifest", type=Path, default=MANIFEST)
    load_cmd.add_argument("--db", default=None, help="기본값: config data.db_path")
    load_cmd.add_argument("--apply", action="store_true", help="실제로 DB에 쓴다")
    args = parser.parse_args()

    if args.command == "build":
        build(args.audits, args.out)
        return 0
    db_path = args.db or load_config("backtest", apply_env=False).config.data.db_path
    return load(args.manifest, db_path, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
