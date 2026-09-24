"""P1/v1 ETH/BTC 페어 추세를 사전등록(docs/research/22)대로 1회 판정한다.

사용 예::

    python scripts/run_p1.py run    # 2018-01-01 ~ 2026-08-31 1회 판정

작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

from auto_trading.backtest.pair_trend import PairBar, PairConfig, PairResult, simulate_pair_trend
from auto_trading.backtest.trend import DailyBar, TrendConfig, simulate_trend

DB = Path("data/trading.db")
OUTPUT = Path("reports/p1")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INITIAL = 10_000.0


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


FROM_MS = _ms(2018, 1, 1)
TO_MS = _ms(2026, 8, 31)
SPLIT_MS = _ms(2022, 1, 1)
MAX_MDD = 0.30


def load() -> tuple[dict[str, dict[int, PairBar]], dict[str, list[tuple[int, float]]]]:
    bars: dict[str, dict[int, PairBar]] = {}
    funding: dict[str, list[tuple[int, float]]] = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for s in SYMBOLS:
            bars[s] = {
                int(ms): PairBar(float(h), float(lo), float(c))
                for ms, h, lo, c in conn.execute(
                    "SELECT open_ms, high, low, close FROM candles WHERE exchange = 'binance_spot'"
                    " AND symbol = ? AND timeframe = '1d' AND open_ms <= ?",
                    (s, TO_MS),
                )
            }
            funding[s] = [
                (int(ms), float(r))
                for ms, r in conn.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                    " AND symbol = ? ORDER BY funding_ms",
                    (s,),
                )
            ]
    return bars, funding


def stats(returns: list[float]) -> dict[str, float | int]:
    n = len(returns)
    mean = statistics.fmean(returns)
    sd = statistics.stdev(returns)
    equity = peak = 1.0
    mdd = 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        mdd = max(mdd, 1 - equity / peak)
    return {
        "days": n,
        "mean_daily": mean,
        "lower_2se": mean - 2 * sd / math.sqrt(n),
        "annual_sharpe": mean / sd * math.sqrt(365) if sd > 0 else math.nan,
        "annual_vol": sd * math.sqrt(365),
        "cagr": equity ** (365 / n) - 1 if equity > 0 else -1.0,
        "mdd": mdd,
    }


def describe(result: PairResult) -> dict[str, Any]:
    pairs = list(zip(result.days, result.returns, strict=True))
    yearly: dict[str, float] = {}
    for d, r in pairs:
        y = str(datetime.fromtimestamp(d / 1000, tz=UTC).year)
        yearly[y] = (1 + yearly.get(y, 0.0)) * (1 + r) - 1
    return {
        "full": stats(result.returns),
        "first_half": stats([r for d, r in pairs if d < SPLIT_MS]),
        "second_half": stats([r for d, r in pairs if d >= SPLIT_MS]),
        "yearly": yearly,
        "price_pnl": result.price_pnl,
        "funding": result.funding,
        "costs": result.costs,
        "trades": result.trades,
        "liquidations": result.liquidations,
        "days_eth_long": result.days_pos,
        "days_eth_short": result.days_neg,
        "final_equity": result.equity[-1],
    }


def judge(report: dict[str, Any], liquidations: int) -> dict[str, Any]:
    full = report["full"]
    criteria = [
        ("P1", full["lower_2se"] > 0, {"lower_2se": full["lower_2se"], "mean": full["mean_daily"]}),
        (
            "P2",
            report["first_half"]["mean_daily"] > 0 and report["second_half"]["mean_daily"] > 0,
            {
                "2018_2021": report["first_half"]["mean_daily"],
                "2022_2026": report["second_half"]["mean_daily"],
            },
        ),
        ("P3", full["mdd"] <= MAX_MDD, {"mdd": full["mdd"], "threshold": MAX_MDD}),
        ("SAFETY", liquidations == 0, {"liquidations": liquidations}),
    ]
    return {
        "status": "PASS" if all(p for _, p, _ in criteria) else "REJECT",
        "criteria": [{"name": n, "passed": p, "detail": d} for n, p, d in criteria],
    }


def portfolio_report(
    pair: PairResult, bars: dict[str, dict[int, PairBar]], funding: dict[str, Any]
) -> dict[str, Any]:
    """보고용: BTC 수익·T/v2와의 상관, T/v2 + P1 50/50 조합."""
    trend_bars = {s: {d: DailyBar(b.high, b.close) for d, b in v.items()} for s, v in bars.items()}
    t2 = simulate_trend(
        trend_bars,
        funding,
        from_ms=FROM_MS,
        to_ms=TO_MS,
        initial_equity=INITIAL,
        cfg=TrendConfig(long_only=True),
    )
    btc = [bars["BTCUSDT"][d].close / bars["BTCUSDT"][d - 86_400_000].close - 1 for d in pair.days]
    combo = [(a + b) / 2 for a, b in zip(pair.returns, t2.returns, strict=True)]
    return {
        "corr_with_btc": statistics.correlation(pair.returns, btc),
        "corr_with_t2_long": statistics.correlation(pair.returns, t2.returns),
        "t2_long_alone": stats(t2.returns),
        "t2_plus_p1_50_50": stats(combo),
    }


def run(output: Path) -> int:
    path = output / "run" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    bars, funding = load()
    pair = simulate_pair_trend(
        bars, funding, from_ms=FROM_MS, to_ms=TO_MS, initial_equity=INITIAL, cfg=PairConfig()
    )
    report = describe(pair)
    verdict = judge(report, pair.liquidations)
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(PairConfig()).items()},
        "verdict": verdict,
        "main": report,
        "report_portfolio": portfolio_report(pair, bars, funding),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"판정: {verdict['status']}")
    for c in verdict["criteria"]:
        print(f"  {c['name']}: {'통과' if c['passed'] else '실패'} {c['detail']}")
    print(f"기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("run",))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return run(parser.parse_args().output)


if __name__ == "__main__":
    sys.exit(main())
