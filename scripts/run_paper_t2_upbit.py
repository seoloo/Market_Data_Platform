"""T/v2-long Paper — 업비트 원화판. 주문 없음.

2026-10-09 사용자 "T/v2 업비트판도 페이퍼에 추가해줘".

사용 예::

    python -m scripts.run_paper_t2_upbit run      # 하루 1 회 (21:02 KST, 바이낸스판 바로 뒤)
    python -m scripts.run_paper_t2_upbit status

* 규칙 · 원장 · 체결은 바이낸스판(`PaperT2Runner`, 문서 16)과 **똑같다**. 시세만 업비트 원화
  (KRW-BTC · KRW-ETH 일봉 · 현재가, 공개 API · 키 없음)로 바꾼다.
* 원장은 바이낸스판 이름(BTCUSDT · ETHUSDT · USDT)을 그대로 쓰지만 **값은 원화**다.
  strategy_id ``t2_upbit`` 로 따로 쌓는다.
* 시작 가상 자본 10,000,000 원. 비용 0.10% (업비트 수수료 0.05% + 체결 불리 0.05%).
* 최소 주문 5,000 원, 수량 단위 0.00000001.
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

import httpx

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import AutoTradingError
from auto_trading.core.types import Candle
from auto_trading.exchange.spot import SpotFilters
from auto_trading.execution.paper_t2 import PaperT2Runner
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_days import PaperDayRepository
from auto_trading.storage.repositories.spot_orders import SpotOrderRepository

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
REPORTS = REPO / "reports" / "paper" / "t2_upbit"
STRATEGY = "t2_upbit"
MARKETS = {"BTCUSDT": "KRW-BTC", "ETHUSDT": "KRW-ETH"}
INITIAL_KRW = Decimal(10_000_000)
COST = Decimal("0.0010")
_DAY = Timeframe.D1.ms
API = "https://api.upbit.com/v1"


class UpbitKrwMarket:
    """``PaperMarket`` — 업비트 원화 공개 시세. 이름은 바이낸스판 심볼로 받는다."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client if client is not None else httpx.Client(timeout=20.0)

    def closed_daily(self, symbol: str, as_of_ms: int) -> list[Candle]:
        rows = self._client.get(
            f"{API}/candles/days", params={"market": MARKETS[symbol], "count": 200}
        ).json()
        out = []
        for r in rows:
            opened = datetime.fromisoformat(str(r["candle_date_time_utc"])).replace(tzinfo=UTC)
            open_ms = int(opened.timestamp() * 1000)
            close_ms = open_ms + _DAY - 1
            if close_ms > as_of_ms:
                continue
            out.append(
                Candle(
                    open_ms=open_ms,
                    close_ms=close_ms,
                    open=Decimal(str(r["opening_price"])),
                    high=Decimal(str(r["high_price"])),
                    low=Decimal(str(r["low_price"])),
                    close=Decimal(str(r["trade_price"])),
                    volume=Decimal(str(r["candle_acc_trade_volume"])),
                    quote_volume=Decimal(str(r["candle_acc_trade_price"])),
                )
            )
        return sorted(out, key=lambda c: c.open_ms)

    def last_price(self, symbol: str) -> Decimal:
        rows = self._client.get(f"{API}/ticker", params={"markets": MARKETS[symbol]}).json()
        return Decimal(str(rows[0]["trade_price"]))

    def filters(self, symbol: str) -> SpotFilters:
        return SpotFilters(Decimal("0.00000001"), Decimal("0.00000001"), Decimal(5000))

    def close(self) -> None:
        self._client.close()


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def run() -> int:
    conn = connect(DB)
    apply_migrations(conn)
    market = UpbitKrwMarket()
    try:
        runner = PaperT2Runner(
            market,
            SpotOrderRepository(conn),
            PaperDayRepository(conn),
            clock=lambda: int(time.time() * 1000),
            initial_quote=INITIAL_KRW,
            cost_rate=COST,
            strategy_id=STRATEGY,
            strategy_tag="t2u",
        )
        day = runner.run_once(int(time.time() * 1000))
    except (AutoTradingError, httpx.HTTPError, KeyError, ValueError) as exc:
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
    print(f"[{_date(day.decision_ms)}] {day.status} · 가상 자본 {day.equity:,.0f} 원")
    print(f"  보유 {day.holdings} (USDT 칸 = 원화)")
    print(f"  내일 체결할 목표 비중 {day.targets}")
    if day.executed:
        print(f"  오늘 체결 {day.executed}")
    if day.note:
        print(f"  메모: {day.note}")
    return 0


def status(limit: int) -> int:
    conn = connect(DB)
    apply_migrations(conn)
    days = PaperDayRepository(conn).recent(STRATEGY, limit)
    conn.close()
    if not days:
        print("업비트 Paper 기록이 아직 없다.")
        return 0
    for d in days:
        print(
            f"  {_date(d.decision_ms)} {d.status:<18} 자본 {d.equity:,.0f} 원"
            f"  체결 {len(d.executed)}건"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    return run() if args.command == "run" else status(args.limit)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
