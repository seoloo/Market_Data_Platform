"""Bronze 증분 받기 · 백필 — 기간을 주면 '아직 없는 파일만' 골라 받는다. 몇 번 돌려도 결과가 같다.

언제 쓰나: 매일 한 번(오케스트레이터 4-6) 또는 사람이 직접. 빠진 날만 다시 받을 때(``--days``).
읽고 쓰는 것:
- 읽기: 체크섬 목록 ``manifests/bronze/binance.csv`` (무엇을 이미 받았나) · Bronze 파일 크기 ·
        원천 ``https://data.binance.vision``
- 쓰기: Bronze 폴더 · 체크섬 목록 · 실행 기록(``MDP_PG_DSN`` 이 있을 때, 작업 이름 ``bronze.sync``)

사용 예::

    python -m mdp.bronze.sync --dataset aggTrades --symbol BTCUSDT --start 2024-03-01
    python -m mdp.bronze.sync --dataset klines --interval 1m --symbol BTCUSDT --start 2026-08-01
    python -m mdp.bronze.sync --dataset aggTrades --symbol BTCUSDT --days 2024-03-05 2024-03-06

어떤 파일을 받나 (:func:`plan_files`):
- 끝난 달은 월 파일 하나, 이번 달은 어제까지 일 파일 (원천이 달이 끝나야 월 파일을 올린다).
- 지난달 월 파일이 아직 안 올라왔으면 그 달은 일 파일로 대신 받는다.
- funding 은 원천에 일 파일이 없다(2026-10-21 확인) — 이번 달 몫은 달이 끝난 뒤 받는다.
- 최근 날짜가 404 면 오류가 아니라 '아직 안 올라옴' — 다음 실행 때 다시 시도된다.

[이유] '이미 받았나' 는 실행 기록 DB 가 아니라 **체크섬 목록 + Bronze 파일**로 판단한다. DB 를 안
       띄운 PC 에서도 같은 결과가 나와야 하고, 목록은 저장소에 커밋돼 있어 기준이 하나다. 목록에
       있고 크기가 같으면 원천에 묻지 않고 건너뛴다(빠름). ``--verify`` 면 전부 다시 대조한다.
"""

from __future__ import annotations

import argparse
import os
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

from mdp.bronze.archive import ArchiveFile, Dataset, Granularity, months_between
from mdp.bronze.fetch import (
    DEFAULT_MANIFEST,
    BronzeFetcher,
    FetchResult,
    FetchStatus,
    default_bronze_root,
    log_json,
    read_manifest,
    record_result,
    setup_cli_logging,
    update_manifest,
)
from mdp.runlog.recorder import NullRecorder, PostgresRecorder, RunRecorder, record_run

__all__ = ["DAILY_DATASETS", "main", "plan_backfill_days", "plan_files", "run_sync"]

DAILY_DATASETS: Final = frozenset({Dataset.AGG_TRADES, Dataset.KLINES})
"""원천에 일 파일도 있는 종류. fundingRate 는 월 파일만 있다(2026-10-21 HEAD 로 확인)."""


def _days(first_day: date, last_day: date) -> list[date]:
    """[쓰임] 두 날 사이(양끝 포함)의 모든 날."""
    return [first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)]


def _month_end(month: date) -> date:
    """[쓰임] 그 달의 마지막 날."""
    next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return next_month - timedelta(days=1)


def _daily_files(
    dataset: Dataset, symbol: str, days: Sequence[date], interval: str | None
) -> list[ArchiveFile]:
    """[쓰임] 날마다 일 파일 이름표."""
    return [ArchiveFile(dataset, symbol, day, Granularity.DAILY, interval) for day in days]


def plan_files(
    dataset: Dataset,
    symbol: str,
    start: date,
    end: date,
    today: date,
    interval: str | None = None,
) -> list[ArchiveFile]:
    """[쓰임] ``start`` ~ ``end``(포함)를 덮는 원천 파일 목록 — 끝난 달은 월 파일, 이번 달은
              어제까지 일 파일. ``end`` 가 오늘 이후면 어제까지로 줄인다.
    [이유] 오늘 파일은 아직 다 쌓이지 않았다. 순수 함수라 '오늘' 을 인자로 받아 시험에서
           고정한다."""
    last_day = min(end, today - timedelta(days=1))
    if last_day < start:
        return []
    current_month = today.replace(day=1)
    files: list[ArchiveFile] = []
    for month in months_between(start, last_day):
        if month < current_month:
            files.append(ArchiveFile(dataset, symbol, month, Granularity.MONTHLY, interval))
        elif dataset in DAILY_DATASETS:
            files += _daily_files(dataset, symbol, _days(max(start, month), last_day), interval)
    return files


def plan_backfill_days(
    dataset: Dataset, symbol: str, days: Sequence[date], today: date, interval: str | None = None
) -> list[ArchiveFile]:
    """[쓰임] 고른 날만 일 파일로 — 월 파일이 있어도 일 파일을 받는다(백필 · 교체용).
    [이유] 연구 216 에서 월 파일이 잘리거나 중복된 날을 일 파일로 바꿔 받아 고쳤다. 그 날들을 Bronze
           에 따로 보관해 두면 Silver 가 어느 쪽을 쓸지 고를 수 있다(교체 규칙은 4-1)."""
    if dataset not in DAILY_DATASETS:
        raise ValueError(f"{dataset} 은(는) 원천에 일 파일이 없다")
    future = [day for day in days if day >= today]
    if future:
        raise ValueError(f"오늘 이후 날짜는 받을 수 없다: {future}")
    return _daily_files(dataset, symbol, sorted(set(days)), interval)


def _known_result(
    fetcher: BronzeFetcher, file: ArchiveFile, manifest: Mapping[str, tuple[int, str]]
) -> FetchResult | None:
    """[쓰임] 체크섬 목록에 있고 Bronze 파일 크기도 같으면 '건너뜀' 결과, 아니면 None."""
    entry = manifest.get(file.relative_path)
    path = fetcher.path_of(file)
    if entry is None or not path.exists() or path.stat().st_size != entry[0]:
        return None
    return FetchResult(file, FetchStatus.SKIPPED_KNOWN, entry[1], entry[0], path)


def _fallback_to_daily(file: ArchiveFile, today: date, start: date, end: date) -> list[ArchiveFile]:
    """[쓰임] 지난달 월 파일이 아직 없을 때 그 달을 대신할 일 파일 목록. 해당 없으면 빈 목록.
    [이유] 원천은 달이 끝나고 며칠 뒤에 월 파일을 올린다. 그동안 그 달 자료가 비지 않게."""
    previous_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    if (
        file.granularity is not Granularity.MONTHLY
        or file.period != previous_month
        or file.dataset not in DAILY_DATASETS
    ):
        return []
    first_day = max(start, file.period)
    last_day = min(end, _month_end(file.period), today - timedelta(days=1))
    return _daily_files(file.dataset, file.symbol, _days(first_day, last_day), file.interval)


def run_sync(
    fetcher: BronzeFetcher,
    files: Sequence[ArchiveFile],
    manifest_path: Path,
    recorder: RunRecorder,
    params: Mapping[str, object],
    *,
    today: date,
    start: date,
    end: date,
    verify: bool = False,
) -> list[FetchResult]:
    """[쓰임] 계획한 파일을 차례로 — 이미 있으면 건너뛰고, 없으면 받고, 지난달 월 파일이 없으면 일
              파일로 바꿔 받는다. 끝에 체크섬 목록을 고친다(실패해도 받은 것까지).
    [이유] 같은 명령을 두 번 돌리면 두 번째는 모두 '건너뜀' 이고 체크섬 목록은 한 줄도 안 바뀐다."""
    manifest = read_manifest(manifest_path)
    results: list[FetchResult] = []
    queue = deque(files)
    try:
        with record_run(recorder, "bronze.sync", params) as run:
            while queue:
                file = queue.popleft()
                known = None if verify else _known_result(fetcher, file, manifest)
                result = known or fetcher.fetch(file)
                results.append(result)
                record_result(run, result)
                if result.status is FetchStatus.MISSING_AT_SOURCE:
                    replacement = _fallback_to_daily(file, today, start, end)
                    if replacement:
                        log_json(
                            event="fallback_daily", path=file.relative_path, days=len(replacement)
                        )
                        queue.extendleft(reversed(replacement))
            missing = [
                r.file.relative_path for r in results if r.status is FetchStatus.MISSING_AT_SOURCE
            ]
            counts = {status.value: 0 for status in FetchStatus}
            for result in results:
                counts[result.status.value] += 1
            log_json(
                event="summary",
                run_id=str(run.run_id),
                job="bronze.sync",
                counts=counts,
                not_yet_published=missing,
            )
    finally:
        changed_count = update_manifest(manifest_path, results)
        log_json(event="manifest", path=str(manifest_path), changed=changed_count)
    return results


def _parse_day(text: str) -> date:
    """[쓰임] ``YYYY-MM-DD`` → 날짜."""
    return date.fromisoformat(text)


def _build_parser() -> argparse.ArgumentParser:
    """[쓰임] 명령줄 인자 정의."""
    parser = argparse.ArgumentParser(
        prog="python -m mdp.bronze.sync", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--dataset", required=True, choices=[d.value for d in Dataset])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", help="봉 간격 (klines 일 때만, 예: 1m)")
    parser.add_argument("--start", type=_parse_day, help="YYYY-MM-DD (--days 가 없을 때 필수)")
    parser.add_argument("--end", type=_parse_day, help="YYYY-MM-DD (기본: 어제)")
    parser.add_argument("--days", type=_parse_day, nargs="+", help="이 날들만 일 파일로 (백필)")
    parser.add_argument(
        "--verify", action="store_true", help="이미 있는 파일도 원천 체크섬과 다시 대조"
    )
    parser.add_argument(
        "--root", type=Path, help="Bronze 위치 (기본: MDP_BRONZE_ROOT 또는 data/lake/bronze)"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="아직 안 올라온 파일이 하나라도 있으면 종료 코드 2 (예약 실행이 나중에 다시 시도하게)",
    )
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("MDP_PG_DSN"),
        help="실행 기록 PostgreSQL 주소 (기본: MDP_PG_DSN, 없으면 기록 안 함)",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, today: date | None = None) -> int:
    """[쓰임] 명령줄 실행 — 계획을 세우고 :func:`run_sync` 로 받는다.
    [이유] ``today`` 는 시험에서만 준다. 평소에는 UTC 오늘(원천 파일 날짜가 UTC 기준)."""
    setup_cli_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    today = today or datetime.now(UTC).date()
    dataset = Dataset(args.dataset)
    if args.days:
        files = plan_backfill_days(dataset, args.symbol, args.days, today, args.interval)
        start, end = min(args.days), max(args.days)
    elif args.start:
        start, end = args.start, args.end or today - timedelta(days=1)
        files = plan_files(dataset, args.symbol, start, end, today, args.interval)
    else:
        parser.error("--start 또는 --days 가 필요하다")
    bronze_root: Path = args.root or default_bronze_root()
    params: dict[str, object] = {
        "dataset": args.dataset,
        "symbol": args.symbol,
        "interval": args.interval,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": [day.isoformat() for day in args.days] if args.days else None,
        "verify": args.verify,
        "bronze_root": str(bronze_root),
    }
    log_json(event="plan", files=len(files), start=start.isoformat(), end=end.isoformat())
    with BronzeFetcher(bronze_root) as fetcher:
        if args.pg_dsn:
            with PostgresRecorder(args.pg_dsn) as recorder:
                results = run_sync(
                    fetcher,
                    files,
                    args.manifest,
                    recorder,
                    params,
                    today=today,
                    start=start,
                    end=end,
                    verify=args.verify,
                )
        else:
            log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이 받는다")
            results = run_sync(
                fetcher,
                files,
                args.manifest,
                NullRecorder(),
                params,
                today=today,
                start=start,
                end=end,
                verify=args.verify,
            )
    missing = [r.file.relative_path for r in results if r.status is FetchStatus.MISSING_AT_SOURCE]
    if args.require_all and missing:
        # [이유] 바이낸스는 하루 파일을 다음 날 올린다 — 매일 예약은 '아직 없음' 을 실패로 알려야
        #        Airflow 가 다시 시도한다(없는 채로 Silver · 대사까지 가지 않게).
        log_json(event="not_yet_published", files=missing)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
