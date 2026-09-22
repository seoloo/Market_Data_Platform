#!/usr/bin/env python
"""유니버스 소속 심볼·기간에 한해 포지셔닝 지표를 받는다.

272개 심볼 × 44개월을 전부 받으면 37만 파일이다. 하지만 필요한 것은
**그 심볼이 유니버스에 들어 있던 달**뿐이라 약 4만 파일로 줄어든다.
유니버스 밖 기간의 지표는 어차피 쓰지 않는다.

체크섬 검증은 그대로 한다. 손상된 지표가 순위를 바꾸면 신호 자체가 틀어진다.

사용 예::

    python scripts/backfill_universe_metrics.py --workers 16
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from calendar import monthrange
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.errors import AutoTradingError  # noqa: E402
from auto_trading.core.types import PerpMetrics  # noqa: E402
from auto_trading.marketdata.archive import BinanceVisionClient, MetricsArchiveFile  # noqa: E402
from auto_trading.marketdata.bulk import DEFAULT_BATCH, run_batched  # noqa: E402
from auto_trading.marketdata.metrics import EARLIEST_AVAILABLE  # noqa: E402
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.metrics import PerpMetricsRepository  # noqa: E402

UNIVERSE = REPO_ROOT / "reports/phase4/universe.json"


def needed_jobs(months_limit: str | None) -> list[tuple[str, list[date]]]:
    """``(심볼, 그 달의 날짜들)`` 작업 목록. 유니버스에 속한 달만 포함한다.

    심볼 단위가 아니라 **월 단위**로 쪼갠다. 44개월 내내 들어 있는 심볼은
    38만 건이 되어 병렬 작업자 수만큼 메모리에 쌓이면 GB 단위가 된다.

    신호가 causal 창을 쓰므로 **직전 달까지** 함께 받는다 — 유니버스 진입 첫날에
    warm-up 이 비어 있으면 그 달 전체가 버려진다.
    """
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    today = datetime.now(UTC).date()
    wanted: dict[tuple[str, int, int], set[date]] = {}
    for month, symbols in sorted(universe.items()):
        if months_limit and month > months_limit:
            continue
        year, mon = (int(part) for part in month.split("-"))
        for offset in (-1, 0):  # 직전 달(warm-up) + 해당 달
            y, m = (year, mon + offset) if mon + offset >= 1 else (year - 1, 12)
            if m > 12:
                y, m = y + 1, 1
            for day in range(1, monthrange(y, m)[1] + 1):
                when = date(y, m, day)
                if when < EARLIEST_AVAILABLE or when > today:
                    continue
                for symbol in symbols:
                    wanted.setdefault((symbol, y, m), set()).add(when)
    return [(symbol, sorted(days)) for (symbol, _, _), days in sorted(wanted.items())]


def fetch_symbol(symbol: str, days: list[date]) -> tuple[str, list[PerpMetrics], int]:
    rows: list[PerpMetrics] = []
    missing = 0
    with BinanceVisionClient(request_delay_s=0.0, max_retries=2) as client:
        for day in days:
            try:
                entries = client.fetch_metrics(MetricsArchiveFile(symbol, day))
            except (AutoTradingError, OSError):
                missing += 1
                continue
            if not entries:
                missing += 1
                continue
            rows.extend(entries)
    return symbol, rows, missing


def already_loaded(conn: sqlite3.Connection, exchange: str, symbol: str, days: list[date]) -> bool:
    """이 심볼·달이 이미 적재됐는가. 재개용이며 하루라도 비면 다시 받는다."""
    start = int(datetime.combine(days[0], datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    end = int(datetime.combine(days[-1], datetime.max.time(), tzinfo=UTC).timestamp() * 1000)
    row = conn.execute(
        "SELECT COUNT(*) FROM perp_metrics WHERE exchange=? AND symbol=? AND at_ms BETWEEN ? AND ?",
        (exchange, symbol, start, end),
    ).fetchone()
    # 하루 288개. 거래소 결측이 있으므로 95% 이상이면 받은 것으로 본다.
    return bool(row) and row[0] >= len(days) * 288 * 0.95


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--until", default=None, help="이 달까지만 (YYYY-MM). 부분 수집용")
    args = parser.parse_args()

    config = load_config().config
    plan = needed_jobs(args.until)
    symbols = {symbol for symbol, _ in plan}
    print(f"심볼 {len(symbols)}개 · 작업 {len(plan):,}개(심볼×월)")

    with Database(REPO_ROOT / config.data.db_path) as db:
        apply_migrations(db.connection)
        repo = PerpMetricsRepository(db.connection, exchange=config.exchange)
        remaining = [
            job
            for job in plan
            if not already_loaded(db.connection, config.exchange, job[0], job[1])
        ]
        print(f"이미 적재됨 {len(plan) - len(remaining):,}개 → 받을 작업 {len(remaining):,}개")

        stats = {"written": 0, "missing": 0}

        def write(results: Iterable[tuple[str, list[PerpMetrics], int]]) -> None:
            for symbol, rows, missing in results:
                if rows:
                    stats["written"] += repo.upsert_many(symbol, rows)
                stats["missing"] += missing

        def progress(done: int, total: int) -> None:
            print(f"  {done}/{total} 작업 · {stats['written']:,}건", flush=True)

        run_batched(
            db,
            remaining,
            lambda job: fetch_symbol(*job),
            write,
            workers=args.workers,
            batch_size=args.batch,
            on_progress=progress,
        )
        total = sum(repo.count(symbol) for symbol in symbols)

    print()
    print(f"기록 {stats['written']:,}건 · 유니버스 지표 총 {total:,}건")
    print(f"파일 없음/빈 파일: {stats['missing']:,}일")
    print(f"완료 {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
