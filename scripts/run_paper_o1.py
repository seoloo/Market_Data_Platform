"""O/v1 Paper 일일 실행 (Phase 6 전향 관찰, docs/research/25).

실제 주문은 없다 — 공개 시세 + 가상 원장.

T/v2 Paper(`run_paper_t2.py`)와 원장이 분리돼 있다. 이 스크립트는 T/v2 기록을 읽지도 쓰지도 않는다.

사용 예::

    python scripts/run_paper_o1.py run      # 오늘(00:00 UTC 이후) 1회. 이미 했으면 보고만 한다
    python scripts/run_paper_o1.py status   # 최근 기록·밀린 날·누락 보정일
    python scripts/run_paper_o1.py verify   # 저장된 원장을 첫날부터 다시 계산해 어긋남 확인

윈도우 작업 스케줄러로 매일 09:10(KST, = 00:10 UTC) 실행한다 — T/v2(09:05) 뒤에 같은 DB를 쓴다.
결과: DB ``paper_overlay_days``, 보고서 ``reports/paper/o1/<날짜>.json``.
데이터가 모자라면 아무것도 기록하지 않고 종료 코드 1로 끝난다(다음 실행이 그날을 다시 계산한다).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from auto_trading.core.errors import AutoTradingError
from auto_trading.core.types import Candle
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly
from auto_trading.exchange.binance_usdm_public import BinanceUsdmPublic
from auto_trading.execution.paper_overlay import PaperOverlayRunner, verify_ledger
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDayRepository

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
REPORTS = REPO / "reports" / "paper" / "o1"
STRATEGY = "o1_overlay"
_DAY = 86_400_000


class BinanceOverlayMarket:
    """현물 공개 시세 + USD-M 공개 시세. 키를 쓰지 않는다."""

    def __init__(self) -> None:
        self._spot = BinanceSpotReadOnly(None)
        self._perp = BinanceUsdmPublic()

    def spot_daily(self, symbol: str, as_of_ms: int, limit: int) -> list[Candle]:
        return self._spot.closed_daily(symbol, as_of_ms, limit)

    def perp_daily(self, symbol: str, as_of_ms: int, limit: int) -> list[Candle]:
        return self._perp.closed_daily(symbol, as_of_ms, limit)

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        return self._perp.funding(symbol, start_ms, end_ms)

    def spot_price(self, symbol: str) -> Decimal:
        return self._spot.last_price(symbol)

    def perp_price(self, symbol: str) -> Decimal:
        return self._perp.last_price(symbol)

    def close(self) -> None:
        self._spot.close()
        self._perp.close()


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def run() -> int:
    conn = connect(DB)
    apply_migrations(conn)
    market = BinanceOverlayMarket()
    try:
        runner = PaperOverlayRunner(
            market, OverlayPaperDayRepository(conn), clock=lambda: int(time.time() * 1000)
        )
        rows = runner.run_once(int(time.time() * 1000))
    except AutoTradingError as exc:
        print(f"실행 실패 — 아무것도 기록하지 않음: {exc}")
        return 1
    finally:
        market.close()
        conn.close()
    REPORTS.mkdir(parents=True, exist_ok=True)
    for row in rows:
        path = REPORTS / f"{_date(row.decision_ms)}.json"
        path.write_text(
            json.dumps(asdict(row), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    today = rows[-1]
    missed = [r for r in rows if r.status == "MISSED"]
    if missed:
        print(
            f"누락 보정 {len(missed)}일 (보유만 계산, 거래 없음): "
            f"{_date(missed[0].decision_ms)} ~ {_date(missed[-1].decision_ms)}"
        )
    t = today.tally
    print(
        f"[{_date(today.decision_ms)}] {today.status} · 가상 자본 {today.equity:,.2f} USDT"
        f" · 노출 {t['exposure']:.2f}배"
    )
    print(f"  현물 {today.state['spot_qty']}")
    print(f"  선물 {today.state['fut_qty']} · 지갑 {today.state['wallet']:,.2f}")
    print(f"  오늘 funding {t['funding']:+.2f} · 비용 {t['costs']:.2f} · 거래 {t['trades']}건")
    if t["liquidations"]:
        print("  ⚠ 선물 청산 발생")
    if t["halted_day"]:
        print("  ⚠ 계좌 정지(−30%) — 선물 추가 중단")
    if t["loss_limit_days"]:
        print("  일 손실 2% — 오늘은 노출 증가 거래 없음")
    print(f"  내일 체결할 T/v2 목표 비중 {today.state['target']}")
    if today.note:
        print(f"  메모: {today.note}")
    return 0


def status(limit: int) -> int:
    conn = connect(DB)
    apply_migrations(conn)
    days = OverlayPaperDayRepository(conn).recent(STRATEGY, limit)
    conn.close()
    if not days:
        print("O/v1 Paper 기록이 아직 없다.")
        return 0
    latest = days[0].decision_ms
    expected = int(time.time() * 1000)
    expected -= expected % _DAY + 1
    behind = (expected - latest) // _DAY
    print(f"최근 실행: {_date(latest)} · 오늘 기준 {behind}일 밀림" + (" ⚠" if behind else " ✓"))
    peak = days[0].state["peak"]  # 고점은 원장이 누적해 들고 있다
    print(
        f"  현재 자본 {days[0].equity:,.2f} · 고점 {peak:,.2f}"
        f" · 고점 대비 {days[0].equity / peak - 1:+.1%}"
    )
    for d in days:
        t = d.tally
        flag = " 청산" if t["liquidations"] else (" 정지" if t["halted_day"] else "")
        print(
            f"  {_date(d.decision_ms)} {d.status:<18} 자본 {d.equity:>10,.2f}"
            f"  노출 {t['exposure']:.2f}  funding {t['funding']:+7.2f}{flag}"
        )
    return 0


def verify() -> int:
    conn = connect(DB)
    apply_migrations(conn)
    rows = OverlayPaperDayRepository(conn).all(STRATEGY)
    conn.close()
    problems = verify_ledger(rows)
    if problems:
        print(f"원장 불일치 {len(problems)}건:")
        for p in problems[:20]:
            print(f"  {p}")
        return 1
    print(f"원장 {len(rows)}일 재계산 일치 ✓")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "verify"))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if args.command == "run":
        return run()
    if args.command == "status":
        return status(args.limit)
    return verify()


if __name__ == "__main__":
    sys.exit(main())
