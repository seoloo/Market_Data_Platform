#!/usr/bin/env python
"""포지셔닝 지표 백필 (H2 선별의 입력).

가격 파생 지표는 전부 실패했다 (docs/phase0/15-h1-postmortem.md).
이 스크립트가 받는 미결제약정·롱숏비·테이커 불균형이 다음 가설의 유일한 새 정보원이다.

사용 예::

    python scripts/backfill_metrics.py --dry-run
    python scripts/backfill_metrics.py --start 2021-01-01 --end 2022-12-31   # TRAIN

일별 파일만 있어 요청 수가 많다(2년 = 730개). 중단해도 안전하며 재실행하면 이어받는다.
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
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.marketdata.archive import BinanceVisionClient  # noqa: E402
from auto_trading.marketdata.metrics import (  # noqa: E402
    EARLIEST_AVAILABLE,
    MetricsBackfiller,
    plan_metrics_files,
)
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.metrics import PerpMetricsRepository  # noqa: E402

logger = logging.getLogger("backfill_metrics")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=EARLIEST_AVAILABLE)
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="기본값은 어제")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--request-delay", type=float, default=0.05)
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    config = load_config().config
    symbol = args.symbol or config.symbol
    end = args.end or (datetime.now(UTC).date() - timedelta(days=1))
    planned = plan_metrics_files(symbol, args.start, end)

    print(f"심볼 : {symbol}")
    print(f"기간 : {args.start} ~ {end}")
    print(f"파일 : {len(planned)}개 (일별)")
    if args.dry_run:
        for archive in planned[:5]:
            print(f"  {archive.url()}")
        if len(planned) > 5:
            print(f"  ... 외 {len(planned) - 5}개")
        return 0
    if not planned:
        print("받을 파일이 없다.")
        return 0

    started = time.monotonic()
    with Database(REPO_ROOT / config.data.db_path) as db:
        applied = apply_migrations(db.connection)
        if applied:
            print(f"마이그레이션 적용: {applied}")
        repo = PerpMetricsRepository(db.connection, exchange=config.exchange)
        before = repo.count(symbol)
        with (
            BinanceVisionClient(request_delay_s=args.request_delay) as client,
            db.transaction(),
        ):
            result = MetricsBackfiller(client, repo).run(
                symbol,
                start=args.start,
                end=end,
                resume=not args.no_resume,
                progress_every=100,
            )
        print("\n" + result.summary())
        for missing in result.files_missing[:10]:
            print(f"  누락: {missing}")
        for error in result.errors[:10]:
            print(f"  오류: {error}")
        print(f"저장된 지표: {before:,} -> {repo.count(symbol):,}건")
        _report_gaps(repo, symbol, args.start, end)

    print(f"소요 시간: {time.monotonic() - started:.1f}초")
    return 0 if result.ok else 1


def _report_gaps(repo: PerpMetricsRepository, symbol: str, start: date, end: date) -> None:
    from_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    to_ms = int(datetime.combine(end, datetime.max.time(), tzinfo=UTC).timestamp() * 1000)
    gaps = repo.find_gaps(symbol, from_ms=from_ms, to_ms=to_ms)
    if not gaps:
        print("결측 없음: OK")
        return
    print(f"[경고] 결측 구간 {len(gaps)}개 - 이 구간에서는 신호를 만들지 않는다:")
    for previous, current in gaps[:10]:
        a = datetime.fromtimestamp(previous / 1000, tz=UTC)
        b = datetime.fromtimestamp(current / 1000, tz=UTC)
        print(
            f"  {a:%Y-%m-%d %H:%M} -> {b:%Y-%m-%d %H:%M} ({(current - previous) / 3600_000:.1f}h)"
        )
    if len(gaps) > 10:
        print(f"  ... 외 {len(gaps) - 10}개")


if __name__ == "__main__":
    raise SystemExit(main())
