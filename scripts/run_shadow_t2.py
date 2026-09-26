"""T/v2 실거래 그림자 운영 — 하루 1회 (Phase 8 준비, docs/research/51). **주문을 보내지 않는다.**

사용 예::

    python scripts/run_shadow_t2.py run --allocation 1000   # 실거래 예정 금액 1,000 USDT 로 1회
    python scripts/run_shadow_t2.py status --allocation 1000

하는 일:
1. Paper 와 같은 실행기를 실거래 예정 금액으로 돌린다 (``spot_orders`` mode='shadow',
   ``paper_days`` strategy_id=``t2_shadow_<금액>``). 금액이 다르면 원장도 다르다.
2. 실계좌를 **읽기만** 한다: 키 권한(읽기 전용·IP 제한이어야 함), 서버 시계, 잔고.
3. 그날 주문 크기로 호가를 읽어 체결 미끄러짐을 추정한다.
4. ``data/HALT`` (긴급 정지 스위치)가 있으면 실거래처럼 그날은 체결하지 않는다.

결과: ``reports/shadow/t2/<날짜>.json`` — 일일 점검(paper_watchdog)이 요약한다.
윈도우 작업 스케줄러로 매일 21:10(KST) 실행한다 (T/v2 Paper 21:00 뒤, 점검 21:20 전).
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
from typing import Any

from auto_trading.config.schema import SpotTrendConfig
from auto_trading.config.secrets import BinanceCredentials, load_binance_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly
from auto_trading.execution.paper_t2 import PaperT2Runner, ledger_balances
from auto_trading.execution.shadow_t2 import book_slippage_bps, readiness_findings
from auto_trading.ops.kill_switch import halted_reason
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
ENV = REPO / ".env"
HALT = REPO / "data" / "HALT"
REPORTS = REPO / "reports" / "shadow" / "t2"
MODE = "shadow"
TAG = "t2s"
BAND = Decimal("0.05")
"""목표와 실제 비중 차이가 이보다 크면 경고 (T/v2 밴드와 같다)."""
PAPER_STRATEGY = "t2_long"
PARITY_TOLERANCE = Decimal("1e-9")
PROBES = (Decimal(100), Decimal(1_000), Decimal(10_000))
"""매일 함께 재는 고정 주문 크기 (USDT)."""
_DAY = 86_400_000


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def strategy_id(allocation: Decimal) -> str:
    return f"t2_shadow_{int(allocation)}"


def _slippage(
    market: BinanceSpotReadOnly, symbol: str, sizes: dict[str, Decimal]
) -> dict[str, Any]:
    bids, asks = market.order_book(symbol)
    mid = (bids[0][0] + asks[0][0]) / 2
    out: dict[str, Any] = {
        "mid": str(mid),
        "spread_bps": str((asks[0][0] - bids[0][0]) / mid * 10_000),
    }
    for label, notional in sizes.items():
        buy = book_slippage_bps(asks, notional, mid=mid)
        sell = book_slippage_bps(bids, notional, mid=mid)
        out[label] = {
            "buy_bps": None if buy is None else f"{buy:.2f}",
            "sell_bps": None if sell is None else f"{sell:.2f}",
        }
    return out


def run(allocation: Decimal) -> int:
    now = int(time.time() * 1000)
    halt = halted_reason(HALT)
    creds: BinanceCredentials | None
    try:
        creds = load_binance_credentials(ENV)
    except AutoTradingError:
        creds = None  # 키가 없어도 원장·호가는 돈다 — 계좌 점검만 경고로 남는다
    market = BinanceSpotReadOnly(creds)
    conn = connect(DB)
    apply_migrations(conn)
    sid = strategy_id(allocation)
    report: dict[str, Any] = {
        "allocation": str(allocation),
        "strategy_id": sid,
        "at_ms": now,
        "orders_sent_to_exchange": 0,
    }
    try:
        orders, days = SpotOrderRepository(conn), PaperDayRepository(conn)
        runner = PaperT2Runner(
            market,
            orders,
            days,
            clock=lambda: int(time.time() * 1000),
            initial_quote=allocation,
            strategy_id=sid,
            strategy_tag=TAG,
            mode=MODE,
        )
        if halt is None:
            day = runner.run_once(now)
            report["day"] = asdict(day)
            report["status"] = day.status
        else:
            report["status"] = "HALTED"
            day = None
        # 실계좌 읽기 (실패해도 그림자 운영은 계속 — 보고만 한다)
        restrictions = balances = offset = None
        try:
            restrictions = market.api_restrictions()
            offset = market.clock_offset_ms()
            balances = market.balances()
        except AutoTradingError as exc:
            report["account_error"] = str(exc)
        symbols = SpotTrendConfig().symbols
        prices = {s: market.last_price(s) for s in symbols}
        # 목표 대비 실제 비중 (최소 주문·수량 단위 영향)
        tracking: list[str] = []
        prev = days.get(sid, (day.decision_ms if day else now - now % _DAY - 1) - _DAY)
        if day is not None and prev is not None and day.status == "OK":
            bal = ledger_balances(
                [r for r in orders.all() if r.mode == MODE and r.strategy_id == sid],
                initial_quote=allocation,
            )
            equity = day.equity
            for s in symbols:
                want = Decimal(prev.targets.get(s) or 0)
                have = bal.get(s.removesuffix("USDT"), Decimal(0)) * prices[s] / equity
                if abs(want - have) > BAND:
                    tracking.append(f"{s} 목표 {want:.3f} vs 실제 {have:.3f}")
        # Paper(t2_long)와 같은 결정일 목표가 같아야 한다 — 같은 신호·같은 코드
        if day is not None:
            paper = days.get(PAPER_STRATEGY, day.decision_ms)
            if paper is None:
                report["paper_parity"] = "Paper 기록 없음 (Paper 가 아직 안 돌았거나 누락)"
            else:
                diff = max(
                    abs(Decimal(paper.targets.get(s) or 0) - Decimal(day.targets.get(s) or 0))
                    for s in symbols
                )
                report["paper_parity"] = f"목표 최대 차이 {diff:.2e}"
                if diff > PARITY_TOLERANCE:
                    tracking.append(f"Paper 와 목표가 다름 (최대 차이 {diff:.2e})")
        report["tracking_gaps"] = tracking
        sizes: dict[str, dict[str, Decimal]] = {
            s: {f"probe_{int(p)}": p for p in PROBES} for s in symbols
        }
        if day is not None:
            for e in day.executed:
                sizes[e["symbol"]]["today_order"] = Decimal(e["quote"])
        report["slippage"] = {s: _slippage(market, s, sizes[s]) for s in symbols}
        findings = readiness_findings(
            account_error=report.get("account_error"),
            restrictions=restrictions,
            clock_offset_ms=offset,
            balances=balances,
            prices=prices,
            allocation=allocation,
            halted_reason=halt,
            rejected_orders=tracking,
        )
        report["findings"] = [asdict(f) for f in findings]
        report["account_value_usdt"] = (
            None
            if balances is None
            else str(
                balances.get("USDT", Decimal(0))
                + sum(balances.get(s.removesuffix("USDT"), Decimal(0)) * prices[s] for s in symbols)
            )
        )
    except AutoTradingError as exc:
        report["status"] = "FAILED"
        report["error"] = str(exc)
    finally:
        market.close()
        conn.close()
    stamp = _date(report["day"]["decision_ms"]) if "day" in report else _date(now - now % _DAY - 1)
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{stamp}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"[{stamp}] 그림자 운영 {report['status']} · 예정 금액 {allocation} USDT · 거래소 주문 0건"
    )
    for f in report.get("findings", []):
        print(f"  {f['level']}: {f['message']}")
    print(f"  보고서 {path.relative_to(REPO)}")
    return 1 if report["status"] == "FAILED" else 0


def status(allocation: Decimal, limit: int) -> int:
    conn = connect(DB)
    apply_migrations(conn)
    rows = PaperDayRepository(conn).recent(strategy_id(allocation), limit)
    conn.close()
    if not rows:
        print("그림자 운영 기록이 아직 없다.")
        return 0
    for d in rows:
        print(
            f"  {_date(d.decision_ms)} {d.status:<18} 자본 {d.equity:.2f}  체결 {len(d.executed)}건"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--allocation", type=Decimal, required=True, help="실거래 예정 금액 (USDT)")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    if args.allocation <= 0:
        parser.error("--allocation 은 양수여야 한다")
    return run(args.allocation) if args.command == "run" else status(args.allocation, args.limit)


if __name__ == "__main__":
    sys.exit(main())
