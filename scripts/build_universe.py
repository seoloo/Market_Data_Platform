#!/usr/bin/env python
"""시점별 거래 가능 유니버스를 만든다 (생존 편향 제거).

**오늘 거래되는 심볼만 골라 과거를 재면 성과가 부풀려진다** —
망한 종목은 롱숏 전략에서 숏 바구니에 들어갔어야 하는데 목록에 없기 때문이다
(docs/phase0/22-multi-symbol-pivot.md §5).

그래서 폐지된 심볼까지 포함한 전체 목록에서, **각 시점에 실제로 거래 가능했고
그 시점 기준으로 유동성이 충분했던** 심볼을 고른다. 미래 정보를 쓰지 않는다.

1일봉만 받는다. 유니버스 선정에는 일 거래대금이면 충분하고, 5분봉은 선정된
심볼에 대해서만 나중에 받는다. 체크섬 검증은 그대로 한다 — 손상된 파일이
유동성 순위를 바꾸면 유니버스 자체가 틀어진다.

사용 예::

    python scripts/build_universe.py --fetch          # 1일봉 수집 (오래 걸림)
    python scripts/build_universe.py --top 30         # 유니버스만 다시 계산
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import sqlite3
import sys
from collections import defaultdict
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
    months_between,
)
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402

COVERAGE = REPO_ROOT / "reports/phase4/symbol_coverage.json"
OUT = REPO_ROOT / "reports/phase4/universe.json"

WINDOW_START = "2023-01"
WINDOW_END = "2026-08"

MIN_MONTHS = 6
"""이보다 짧게 존재한 심볼은 받지 않는다. 신규 상장 직후의 비정상 구간만 남는다."""

LOOKBACK_DAYS = 30
"""유동성 판정 창. **그 시점까지의** 거래대금만 본다 (causal)."""

MIN_DAILY_QUOTE = 20_000_000
"""일 거래대금 하한 (USDT). 이보다 작으면 슬리피지를 신뢰할 수 없다."""

NON_CRYPTO_PREFIXES = (
    "XAU",
    "XAG",  # 금·은
    "NVDA",
    "MSTR",
    "INTC",
    "CRCL",
    "EWY",  # 토큰화 주식·ETF
    "TSLA",
    "AAPL",
    "META",
    "GOOGL",
    "AMZN",
    "MSFT",
    "COIN",
    "HOOD",
    "SPY",
    "QQQ",
    "GLD",
    "PLTR",
)
"""유니버스에서 제외할 비암호 기초자산.

Binance USD-M 에는 금·은·주식을 기초로 한 무기한 선물이 있다 (2026 유니버스의 10%).
H3 의 메커니즘은 **암호화폐 무기한 선물의 레버리지 포지셔닝 해소**인데, 이들은
실물 기초자산이 가격을 지배하고 funding·OI 의 의미도 다르다. 섞으면 가설이 말하는
것과 다른 것을 재게 된다.

⚠ 이 제외는 **신호를 돌리기 전에** 정했다. 성과를 보고 고른 것이 아니라
기초자산 종류로 나눈 구조적 판단이다.
"""


def candidates() -> list[str]:
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    return sorted(
        symbol
        for symbol, info in coverage.items()
        if info["last"] >= WINDOW_START
        and info["first"] <= WINDOW_END
        and info["months"] >= MIN_MONTHS
    )


def fetch_symbol(symbol: str, start: date, end: date) -> tuple[str, list[Candle], int]:
    """한 심볼의 1일봉 전체. 없는 달은 건너뛴다 (상장 전/폐지 후)."""
    out: list[Candle] = []
    missing = 0
    with BinanceVisionClient(request_delay_s=0.0, max_retries=2) as client:
        for month in months_between(start, end):
            archive = ArchiveFile(symbol, Timeframe.D1, month, Granularity.MONTHLY)
            try:
                bars = client.fetch_candles(archive)
            except (AutoTradingError, OSError):
                missing += 1
                continue
            if bars is None:
                missing += 1
                continue
            out.extend(bars)
    return symbol, out, missing


def fetch_all(symbols: list[str], start: date, end: date, workers: int) -> None:
    config = load_config().config
    with Database(REPO_ROOT / config.data.db_path) as db:
        apply_migrations(db.connection)
        repo = CandleRepository(db.connection, exchange=config.exchange)
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        done = 0
        with (
            futures.ThreadPoolExecutor(max_workers=workers) as pool,
            db.transaction(),
        ):
            jobs = {pool.submit(fetch_symbol, s, start, end): s for s in symbols}
            for job in futures.as_completed(jobs):
                symbol, bars, _ = job.result()
                if bars:
                    repo.upsert_many(symbol, Timeframe.D1, bars, source="bulk", ingested_ms=now_ms)
                done += 1
                if done % 50 == 0:
                    print(f"  {done}/{len(symbols)}", flush=True)


def build(top: int) -> dict[str, list[str]]:
    """월 초마다 **직전 30일** 거래대금 상위 ``top`` 개를 고른다.

    미래를 보지 않는다 — 각 월의 선정에는 그 월 시작 **이전** 데이터만 쓴다.
    """
    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    daily: dict[str, dict[int, float]] = defaultdict(dict)
    for row in conn.execute(
        "SELECT symbol, open_ms, quote_volume FROM candles WHERE timeframe = '1d'"
    ):
        daily[row["symbol"]][int(row["open_ms"])] = float(row["quote_volume"])

    universe: dict[str, list[str]] = {}
    for month in months_between(date(2023, 1, 1), date(2026, 8, 1)):
        as_of = int(datetime(month.year, month.month, 1, tzinfo=UTC).timestamp() * 1000)
        window_start = as_of - LOOKBACK_DAYS * 86_400_000
        ranked: list[tuple[float, str]] = []
        for symbol, series in daily.items():
            if symbol.startswith(NON_CRYPTO_PREFIXES):
                continue
            values = [v for ms, v in series.items() if window_start <= ms < as_of]
            if len(values) < LOOKBACK_DAYS // 2:
                continue  # 상장 직후거나 이미 폐지됨
            average = sum(values) / len(values)
            if average < MIN_DAILY_QUOTE:
                continue
            ranked.append((average, symbol))
        ranked.sort(reverse=True)
        universe[month.strftime("%Y-%m")] = [symbol for _, symbol in ranked[:top]]
    return universe


def report(universe: dict[str, list[str]]) -> None:
    members = sorted({s for names in universe.values() for s in names})
    print(f"\n월별 유니버스 {len(universe)}개월 · 등장한 심볼 총 {len(members)}개")
    first, last = min(universe), max(universe)
    print(f"  {first}: {', '.join(universe[first][:8])} ...")
    print(f"  {last}: {', '.join(universe[last][:8])} ...")

    entered = set(universe[last]) - set(universe[first])
    exited = set(universe[first]) - set(universe[last])
    print(f"\n첫 달에는 있었으나 마지막 달에 없는 심볼 {len(exited)}개:")
    print("  " + ", ".join(sorted(exited)))
    print(f"나중에 들어온 심볼 {len(entered)}개:")
    print("  " + ", ".join(sorted(entered)[:20]))
    turnover = [
        len(set(universe[a]) ^ set(universe[b])) / 2
        for a, b in zip(sorted(universe), sorted(universe)[1:], strict=False)
    ]
    print(f"\n월평균 교체 {sum(turnover) / len(turnover):.1f}개 — 고정 목록이 아니다")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="1일봉을 받는다")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    symbols = candidates()
    print(f"후보 심볼 {len(symbols)}개 (구간 내 {MIN_MONTHS}개월 이상 존재)")
    if args.fetch:
        print("1일봉 수집 중...")
        fetch_all(symbols, date(2022, 11, 1), date(2026, 8, 1), args.workers)

    universe = build(args.top)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(universe, ensure_ascii=False, indent=0), encoding="utf-8")
    report(universe)
    print(f"\n기록: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
