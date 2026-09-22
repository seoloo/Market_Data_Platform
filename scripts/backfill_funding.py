#!/usr/bin/env python
"""funding rate 백필 실행 스크립트.

**이 데이터가 없으면 아무 진입도 일어나지 않는다.** 리스크 게이트가 진입 직전에
`funding_rate` / `next_funding_ms` 를 요구하고, 없으면 `DATA_QUALITY` 로 거부한다
(fail-closed). 즉 Phase 3 백테스트의 선행 조건이다.

사용 예::

    # 계획만 출력
    python scripts/backfill_funding.py --dry-run

    # 전체 (2019-09 ~ 지난달). 수십 초.
    python scripts/backfill_funding.py

    # 구간 지정
    python scripts/backfill_funding.py --start 2024-01-01 --end 2024-12-31

중단해도 안전하다. 다시 실행하면 저장된 마지막 정산이 속한 달부터 이어받는다.
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

# Windows 기본 콘솔은 cp949 라 한글 타이포그래피 기호에서 죽는다.
# 리포트 출력 때문에 스크립트가 실패하면 안 되므로 UTF-8 로 고정한다.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.marketdata.archive import BinanceVisionClient  # noqa: E402
from auto_trading.marketdata.backfill import DEFAULT_START  # noqa: E402
from auto_trading.marketdata.funding import (  # noqa: E402
    FundingBackfiller,
    plan_funding_files,
)
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.funding import FundingRateRepository  # noqa: E402

logger = logging.getLogger("backfill_funding")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="기본값은 어제. 진행 중인 달은 아카이브가 없으므로 자동 제외된다.",
    )
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--dry-run", action="store_true", help="받을 파일만 출력한다")
    parser.add_argument("--no-resume", action="store_true", help="처음부터 다시 받는다")
    parser.add_argument("--request-delay", type=float, default=0.1)
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    config = load_config().config
    symbol = args.symbol or config.symbol
    end = args.end or (datetime.now(UTC).date() - timedelta(days=1))
    files = plan_funding_files(symbol, args.start, end)

    print(f"심볼   : {symbol}")
    print(f"기간   : {args.start} ~ {end}")
    print(f"파일   : {len(files)}개 (월별)")
    if args.dry_run:
        for archive in files:
            print(f"  {archive.url()}")
        return 0
    if not files:
        print("받을 파일이 없다 (구간이 진행 중인 달 안에 있다).")
        return 0

    started = time.monotonic()
    db_path = REPO_ROOT / config.data.db_path
    with Database(db_path) as db:
        applied = apply_migrations(db.connection)
        if applied:
            logger.info("마이그레이션 적용: %s", applied)

        repo = FundingRateRepository(db.connection, exchange=config.exchange)
        before = repo.count(symbol)
        with (
            BinanceVisionClient(request_delay_s=args.request_delay) as client,
            db.transaction(),
        ):
            result = FundingBackfiller(client, repo).run(
                symbol, start=args.start, end=end, resume=not args.no_resume
            )

        print("\n" + result.summary())
        for missing in result.files_missing:
            logger.warning("누락: %s", missing)
        for error in result.errors:
            logger.error("%s", error)

        total = repo.count(symbol)
        print(f"저장된 funding: {before:,} → {total:,}건")
        _report_gaps(repo, symbol, args.start, end)

    print(f"소요 시간: {time.monotonic() - started:.1f}초")
    return 0 if result.ok else 1


def _report_gaps(repo: FundingRateRepository, symbol: str, start: date, end: date) -> None:
    from_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    to_ms = int(datetime.combine(end, datetime.max.time(), tzinfo=UTC).timestamp() * 1000)
    gaps = repo.find_gaps(symbol, from_ms=from_ms, to_ms=to_ms)
    if not gaps:
        print("결측 없음: OK")
        return
    print(f"[경고] 결측 구간 {len(gaps)}개 - 이 구간에서는 진입이 거부된다:")
    for previous, current in gaps[:10]:
        prev_dt = datetime.fromtimestamp(previous / 1000, tz=UTC)
        next_dt = datetime.fromtimestamp(current / 1000, tz=UTC)
        print(f"  {prev_dt:%Y-%m-%d %H:%M} → {next_dt:%Y-%m-%d %H:%M}")
    if len(gaps) > 10:
        print(f"  ... 외 {len(gaps) - 10}개")


if __name__ == "__main__":
    raise SystemExit(main())
