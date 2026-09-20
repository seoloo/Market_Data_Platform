#!/usr/bin/env python
"""과거 캔들 백필 + 무결성 검증 실행 스크립트 (게이트 1).

data.binance.vision 공개 아카이브에서 캔들을 받아 DB 에 적재하고,
갭·재집계 대조·이상치 검사를 수행한다.

사용 예::

    # 1개월치로 동작 확인 (수 초, 수십 MB)
    python scripts/backfill.py --start 2024-01-01 --end 2024-01-31 --timeframes 1m 5m

    # 전체 (2019-09 ~ 어제). 30~60분, 1~2GB
    python scripts/backfill.py

    # 이미 받은 데이터만 검증
    python scripts/backfill.py --integrity-only

    # 계획만 출력하고 받지 않음
    python scripts/backfill.py --dry-run

중단해도 안전하다. 다시 실행하면 이미 받은 구간은 건너뛴다.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:  # 설치 없이 실행할 수 있게
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.marketdata.archive import ArchiveFile, BinanceVisionClient  # noqa: E402
from auto_trading.marketdata.backfill import (  # noqa: E402
    DEFAULT_START,
    Backfiller,
    plan_archive_files,
)
from auto_trading.marketdata.integrity import IntegrityChecker  # noqa: E402
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402

logger = logging.getLogger("backfill")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="과거 캔들 백필 + 무결성 검증",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--symbol", default=None, help="기본값은 config 의 symbol")
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        metavar="TF",
        help="기본값은 config 의 data.timeframes (예: 1m 5m 15m 1h 4h 1d)",
    )
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (기본: 2019-09-08 상장일)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (기본: 어제 UTC)")
    parser.add_argument("--db", default=None, help="기본값은 config 의 data.db_path")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--mode", default="backtest")
    parser.add_argument("--no-resume", action="store_true", help="이미 받은 구간도 다시 받는다")
    parser.add_argument(
        "--no-verify-checksum",
        action="store_true",
        help="체크섬 검증을 건너뛴다 (권장하지 않음)",
    )
    parser.add_argument("--integrity-only", action="store_true", help="다운로드 없이 검증만")
    parser.add_argument("--skip-integrity", action="store_true", help="검증 없이 다운로드만")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력")
    parser.add_argument("--delay", type=float, default=0.05, help="요청 간 대기 (초)")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()


def resolve_timeframes(raw: list[str] | None, fallback: tuple[Timeframe, ...]) -> list[Timeframe]:
    if raw is None:
        return list(fallback)
    try:
        return [Timeframe(value) for value in raw]
    except ValueError as exc:
        valid = ", ".join(t.value for t in Timeframe)
        raise SystemExit(f"알 수 없는 타임프레임. 가능한 값: {valid}") from exc


def parse_date(value: str | None, default: date) -> date:
    if value is None:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC).date()
    except ValueError as exc:
        raise SystemExit(f"날짜 형식은 YYYY-MM-DD 여야 한다: {value!r}") from exc


def print_plan(symbol: str, timeframes: list[Timeframe], start: date, end: date) -> None:
    total = 0
    print(f"\n{'타임프레임':<8} {'파일 수':>8}  범위")
    print("-" * 60)
    for timeframe in timeframes:
        plan = plan_archive_files(symbol, timeframe, start, end)
        total += len(plan)
        print(f"{timeframe.value:<8} {len(plan):>8}  {start} ~ {end}")
    print("-" * 60)
    print(f"{'합계':<8} {total:>8} 개 파일 (+ 체크섬 {total}개 요청)\n")


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx 는 요청마다 INFO 를 남겨 진행 표시를 덮어버린다.
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    loaded = load_config(args.mode, config_dir=args.config_dir, apply_env=False)
    config = loaded.config
    symbol = args.symbol or config.symbol
    timeframes = resolve_timeframes(args.timeframes, config.data.timeframes)
    db_path = Path(args.db or config.data.db_path)

    yesterday = datetime.now(tz=UTC).date() - timedelta(days=1)
    start = parse_date(args.start, DEFAULT_START)
    end = parse_date(args.end, yesterday)
    if start > end:
        raise SystemExit(f"start({start}) 가 end({end}) 보다 나중이다")

    print(f"\n심볼      : {symbol}")
    print(f"타임프레임: {', '.join(t.value for t in timeframes)}")
    print(f"기간      : {start} ~ {end}")
    print(f"DB        : {db_path}")
    print(f"설정 해시 : {loaded.hash[:12]}")

    if args.dry_run:
        print_plan(symbol, timeframes, start, end)
        return 0

    started = time.monotonic()
    with Database(db_path) as db:
        applied = apply_migrations(db.connection)
        if applied:
            logger.info("마이그레이션 적용: %s", applied)

        repo = CandleRepository(db.connection, exchange=config.exchange)

        if not args.integrity_only:
            _run_backfill(args, repo, symbol, timeframes, start, end)

        if not args.skip_integrity:
            _run_integrity(repo, symbol, timeframes)

        _print_counts(repo, symbol, timeframes)

    print(f"\n소요 시간: {time.monotonic() - started:.1f}초")
    return 0


def _run_backfill(
    args: argparse.Namespace,
    repo: CandleRepository,
    symbol: str,
    timeframes: list[Timeframe],
    start: date,
    end: date,
) -> None:
    print("\n" + "=" * 70)
    print("백필")
    print("=" * 70)

    ingested_ms = int(time.time() * 1000)
    client = BinanceVisionClient(max_retries=3, request_delay_s=args.delay)
    backfiller = Backfiller(client, repo, verify_checksum=not args.no_verify_checksum)

    try:
        for timeframe in timeframes:
            plan_size = len(plan_archive_files(symbol, timeframe, start, end))
            done = 0

            def progress(archive_file: ArchiveFile, written: int, _total: int = plan_size) -> None:
                nonlocal done
                done += 1
                print(f"  [{done:>4}/{_total}] {archive_file}  {written:,}봉", flush=True)

            result = backfiller.run(
                symbol,
                timeframe,
                start=start,
                end=end,
                ingested_ms=ingested_ms,
                resume=not args.no_resume,
                progress=progress,
            )
            print(f"  -> {result.summary()}")
            for error in result.errors[:5]:
                print(f"     오류: {error}")
            if len(result.errors) > 5:
                print(f"     ... 외 {len(result.errors) - 5}건")
    finally:
        client.close()


def _run_integrity(repo: CandleRepository, symbol: str, timeframes: list[Timeframe]) -> None:
    print("\n" + "=" * 70)
    print("무결성 검증 (게이트 1)")
    print("=" * 70)

    report = IntegrityChecker(repo).run(symbol, timeframes)
    print(f"\n{report.summary()}")

    if report.issues:
        print("\n상위 20건:")
        for issue in report.issues[:20]:
            when = datetime.fromtimestamp(issue.from_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
            print(f"  {when}  {issue}")
        if len(report.issues) > 20:
            print(f"  ... 외 {len(report.issues) - 20}건")

    print("\n" + ("게이트 1 통과" if report.ok else "게이트 1 실패 — critical 문제 해결 필요"))


def _print_counts(repo: CandleRepository, symbol: str, timeframes: list[Timeframe]) -> None:
    print("\n" + "=" * 70)
    print("적재 현황")
    print("=" * 70)
    print(f"{'TF':<6} {'봉 수':>12}  {'시작':<12} {'끝':<12}")
    print("-" * 70)
    for timeframe in timeframes:
        count = repo.count(symbol, timeframe)
        first = repo.first_open_ms(symbol, timeframe)
        last = repo.last_open_ms(symbol, timeframe)
        fmt = "%Y-%m-%d"
        first_text = datetime.fromtimestamp(first / 1000, tz=UTC).strftime(fmt) if first else "-"
        last_text = datetime.fromtimestamp(last / 1000, tz=UTC).strftime(fmt) if last else "-"
        print(f"{timeframe.value:<6} {count:>12,}  {first_text:<12} {last_text:<12}")


if __name__ == "__main__":
    raise SystemExit(main())
