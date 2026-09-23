"""T/v2-long Paper 일일 실행 (Phase 6). 실제 주문은 없다 — 공개 시세 + 가상 체결.

사용 예::

    python scripts/run_paper_t2.py run      # 오늘(00:00 UTC 이후) 1회. 이미 했으면 보고만 한다
    python scripts/run_paper_t2.py status   # 최근 기록·누락일 (watchdog 점검)

윈도우 작업 스케줄러로 매일 09:05(KST, = 00:05 UTC) 실행한다. 경로는 모두 프로젝트 기준 절대 경로다.
결과: DB ``paper_days``·``spot_orders``(mode='paper'), 보고서 ``reports/paper/t2/<날짜>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly
from auto_trading.execution.paper_t2 import PaperT2Runner
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_days import PaperDayRepository
from auto_trading.storage.repositories.spot_orders import SpotOrderRepository

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
REPORTS = REPO / "reports" / "paper" / "t2"
STRATEGY = "t2_long"
_DAY = 86_400_000


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def run() -> int:
    conn = connect(DB)
    apply_migrations(conn)
    market = BinanceSpotReadOnly(None)  # 공개 시세만 — 키를 쓰지 않는다
    try:
        runner = PaperT2Runner(
            market,
            SpotOrderRepository(conn),
            PaperDayRepository(conn),
            clock=lambda: int(time.time() * 1000),
        )
        day = runner.run_once(int(time.time() * 1000))
    except AutoTradingError as exc:
        print(f"실행 실패: {exc}")
        return 1
    finally:
        market.close()
        conn.close()
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{_date(day.decision_ms)}.json"
    path.write_text(
        json.dumps(asdict(day), ensure_ascii=False, indent=1, default=str, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"[{_date(day.decision_ms)}] {day.status} · 가상 자본 {day.equity:.2f} USDT")
    print(f"  보유 {day.holdings}")
    print(f"  내일 체결할 목표 비중 {day.targets}")
    if day.executed:
        print(f"  오늘 체결 {day.executed}")
    if day.note:
        print(f"  메모: {day.note}")
    print(f"  보고서 {path.relative_to(REPO)}")
    return 0


def status(limit: int) -> int:
    conn = connect(DB)
    apply_migrations(conn)
    days = PaperDayRepository(conn).recent(STRATEGY, limit)
    conn.close()
    if not days:
        print("Paper 기록이 아직 없다.")
        return 0
    latest = days[0].decision_ms
    expected = int(time.time() * 1000)
    expected -= expected % _DAY + 1
    behind = (expected - latest) // _DAY
    print(f"최근 실행: {_date(latest)} · 오늘 기준 {behind}일 밀림" + (" ⚠" if behind else " ✓"))
    for newer, older in pairwise(days):
        gap = (newer.decision_ms - older.decision_ms) // _DAY - 1
        if gap:
            print(f"  누락 {gap}일: {_date(older.decision_ms)} ~ {_date(newer.decision_ms)} 사이")
    for d in days:
        print(
            f"  {_date(d.decision_ms)} {d.status:<18} 자본 {d.equity:.2f}  체결 {len(d.executed)}건"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    return run() if args.command == "run" else status(args.limit)


if __name__ == "__main__":
    sys.exit(main())
