#!/usr/bin/env python
"""유니버스 소속 심볼·기간의 5분봉을 받는다 (선행수익 계산용).

지표와 같은 원칙이다 — **유니버스에 속한 달만** 받는다. 272개 심볼의 전 기간을
받을 이유가 없다. 5분봉은 월별 아카이브라 지표(일별)보다 파일 수가 훨씬 적다.

사용 예::

    python scripts/backfill_universe_klines.py --workers 8
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
from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.errors import AutoTradingError  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.marketdata.archive import (  # noqa: E402
    ArchiveFile,
    BinanceVisionClient,
    Granularity,
)
from auto_trading.marketdata.bulk import DEFAULT_BATCH, run_batched  # noqa: E402
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402

UNIVERSE = REPO_ROOT / "reports/phase4/universe.json"


def needed_months() -> list[tuple[str, date]]:
    """``(심볼, 달)`` 목록. 해당 달 + **다음 달**을 받는다.

    다음 달까지 받는 이유: 월 마지막 날의 6시간 선행수익이 다음 달로 넘어간다.
    빠뜨리면 매월 말 관측이 조용히 사라진다.
    """
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    today = datetime.now(UTC).date()
    wanted: set[tuple[str, date]] = set()
    for month, symbols in universe.items():
        year, mon = (int(part) for part in month.split("-"))
        for offset in (0, 1):
            y, m = (year, mon + offset) if mon + offset <= 12 else (year + 1, 1)
            when = date(y, m, 1)
            if when > today.replace(day=1):
                continue
            wanted.update((symbol, when) for symbol in symbols)
    return sorted(wanted)


def fetch(symbol: str, month: date) -> tuple[str, list[Candle]]:
    archive = ArchiveFile(symbol, Timeframe.M5, month, Granularity.MONTHLY)
    with BinanceVisionClient(request_delay_s=0.0, max_retries=2) as client:
        try:
            bars = client.fetch_candles(archive)
        except (AutoTradingError, OSError):
            return symbol, []
    return symbol, list(bars or ())


def already_loaded(conn: sqlite3.Connection, exchange: str, symbol: str, month: date) -> bool:
    """이 심볼·달의 5분봉이 이미 있는가 (재개용)."""
    start = int(datetime(month.year, month.month, 1, tzinfo=UTC).timestamp() * 1000)
    end = start + monthrange(month.year, month.month)[1] * 86_400_000
    row = conn.execute(
        "SELECT COUNT(*) FROM candles"
        " WHERE exchange=? AND symbol=? AND timeframe='5m' AND open_ms >= ? AND open_ms < ?",
        (exchange, symbol, start, end),
    ).fetchone()
    expected = monthrange(month.year, month.month)[1] * 288
    return bool(row) and row[0] >= expected * 0.95


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    args = parser.parse_args()

    config = load_config().config
    plan = needed_months()
    symbols = {symbol for symbol, _ in plan}
    print(f"심볼 {len(symbols)}개 · 작업 {len(plan):,}개(심볼×월)")

    with Database(REPO_ROOT / config.data.db_path) as db:
        apply_migrations(db.connection)
        repo = CandleRepository(db.connection, exchange=config.exchange)
        remaining = [
            job
            for job in plan
            if not already_loaded(db.connection, config.exchange, job[0], job[1])
        ]
        print(f"이미 적재됨 {len(plan) - len(remaining):,}개 → 받을 작업 {len(remaining):,}개")

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        stats = {"written": 0, "missing": 0}

        def write(results: Iterable[tuple[str, list[Candle]]]) -> None:
            for symbol, bars in results:
                if bars:
                    stats["written"] += repo.upsert_many(
                        symbol, Timeframe.M5, bars, source="bulk", ingested_ms=now_ms
                    )
                else:
                    stats["missing"] += 1

        def progress(done: int, total: int) -> None:
            print(f"  {done}/{total} · {stats['written']:,}봉", flush=True)

        run_batched(
            db,
            remaining,
            lambda job: fetch(*job),
            write,
            workers=args.workers,
            batch_size=args.batch,
            on_progress=progress,
        )
        total = sum(repo.count(symbol, Timeframe.M5) for symbol in symbols)

    print()
    print(f"기록 {stats['written']:,}봉 · 파일 없음 {stats['missing']}개 · 총 {total:,}봉")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
