#!/usr/bin/env python
"""유니버스 소속 심볼·기간의 funding rate 를 받는다.

2026-09-22 에 발견한 결함을 메운다 — ``funding_rates`` 에 BTCUSDT 하나만 있었다.
``X1_rel_funding`` 은 사전 등록된 신호 5개 중 하나이므로 (``docs/phase0/23`` §2),
이 데이터 없이 선별을 돌리면 X1 은 표본 0으로 자동 탈락한다.
**없는 엣지가 아니라 없는 데이터 때문에** 떨어지는 것이고, 그건 기각이 아니라
측정 실패다.

지표와 달리 warm-up 이 필요 없다. 신호는 z-score 가 아니라 **마지막 정산 요율**을
그대로 쓴다. 다만 달 첫날에 이미 정산된 값이 있어야 하므로 직전 달까지 받는다.

사용 예::

    python scripts/backfill_universe_funding.py --workers 12
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
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
from auto_trading.core.types import FundingRate  # noqa: E402
from auto_trading.marketdata.archive import BinanceVisionClient, FundingArchiveFile  # noqa: E402
from auto_trading.marketdata.bulk import DEFAULT_BATCH, run_batched  # noqa: E402
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.funding import FundingRateRepository  # noqa: E402

UNIVERSE = REPO_ROOT / "reports/phase4/universe.json"

MIN_PER_MONTH = 28 * 3
"""이 달을 "받았다" 고 볼 최소 정산 건수 (8시간 주기 × 28일).

실제로는 90 전후다. 상장 중간 달은 이 값을 못 채워 매번 다시 받게 되지만,
덜 받은 달을 받았다고 착각하는 쪽보다 낫다.
"""

Job = tuple[str, date]
Fetched = tuple[str, tuple[FundingRate, ...], bool]


def needed_jobs(until: str | None) -> list[Job]:
    """``(심볼, 그 달의 1일)`` 작업 목록. 유니버스 소속 달 + 직전 달."""
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    this_month = datetime.now(UTC).date().replace(day=1)
    wanted: set[Job] = set()
    for month, symbols in sorted(universe.items()):
        if until and month > until:
            continue
        year, mon = (int(part) for part in month.split("-"))
        for offset in (-1, 0):  # 직전 달은 달 첫날의 "마지막 정산" 을 위해 받는다
            y, m = (year, mon + offset) if mon + offset >= 1 else (year - 1, 12)
            first = date(y, m, 1)
            if first >= this_month:  # 진행 중인 달은 아카이브가 없다
                continue
            wanted.update((symbol, first) for symbol in symbols)
    return sorted(wanted)


def fetch_month(symbol: str, first: date) -> Fetched:
    """한 심볼·달. 파일이 없으면 ``missing=True`` 로 돌려준다 (예외로 올리지 않는다).

    유니버스에 든 심볼이라도 상장 전 달은 아카이브가 없는 게 정상이다.
    """
    archive = FundingArchiveFile(symbol=symbol, period=first)
    try:
        with BinanceVisionClient(request_delay_s=0.0, max_retries=2) as client:
            rates = client.fetch_funding(archive)
    except (AutoTradingError, OSError):
        return symbol, (), True
    if not rates:
        return symbol, (), True
    return symbol, rates, False


def already_loaded(conn: sqlite3.Connection, exchange: str, symbol: str, first: date) -> bool:
    """이 심볼·달이 이미 적재됐는가. 재개용."""
    nxt = date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)
    bounds = tuple(
        int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
        for day in (first, nxt)
    )
    row = conn.execute(
        "SELECT COUNT(*) FROM funding_rates"
        " WHERE exchange=? AND symbol=? AND funding_ms >= ? AND funding_ms < ?",
        (exchange, symbol, *bounds),
    ).fetchone()
    return bool(row) and row[0] >= MIN_PER_MONTH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--until", default=None, help="이 달까지만 (YYYY-MM)")
    args = parser.parse_args()

    config = load_config().config
    plan = needed_jobs(args.until)
    symbols = {symbol for symbol, _ in plan}
    print(f"심볼 {len(symbols)}개 · 작업 {len(plan):,}개(심볼×월)")

    with Database(REPO_ROOT / config.data.db_path) as db:
        apply_migrations(db.connection)
        repo = FundingRateRepository(db.connection, exchange=config.exchange)
        remaining = [
            job for job in plan if not already_loaded(db.connection, config.exchange, *job)
        ]
        print(f"이미 적재됨 {len(plan) - len(remaining):,}개 → 받을 작업 {len(remaining):,}개")

        stats = {"written": 0, "missing": 0}

        def write(results: Iterable[Fetched]) -> None:
            for symbol, rates, missing in results:
                if rates:
                    stats["written"] += repo.upsert_many(symbol, rates)
                stats["missing"] += int(missing)

        def progress(done: int, total: int) -> None:
            print(f"  {done}/{total} 작업 · {stats['written']:,}건", flush=True)

        run_batched(
            db,
            remaining,
            lambda job: fetch_month(*job),
            write,
            workers=args.workers,
            batch_size=args.batch,
            on_progress=progress,
        )
        covered = sum(1 for symbol in symbols if repo.count(symbol) > 0)

    print()
    print(f"기록 {stats['written']:,}건 · 심볼 {covered}/{len(symbols)}개에 funding 존재")
    print(f"파일 없음: {stats['missing']:,}개월")
    print(f"완료 {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
