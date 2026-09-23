"""T/v1 시계열 추세 추종을 사전등록(docs/research/15)대로 1회 판정하고, 이후 전향 관찰한다.

사용 예::

    python scripts/run_t_trend.py run       # 2018-01-01 ~ 2026-08-31 1회 판정
    python scripts/run_t_trend.py forward   # 2026-09-01 이후 전향 보고 (판정 없음)

run은 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
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

from auto_trading.backtest.trend import DailyBar, TrendConfig, TrendResult, simulate_trend

DB = Path("data/trading.db")
OUTPUT = Path("reports/t")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INITIAL_EQUITY = 10_000.0
_DAY = 86_400_000


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


FROM_MS = _ms(2018, 1, 1)
TO_MS = _ms(2026, 8, 31)
HALVES = ((_ms(2018, 1, 1), _ms(2022, 1, 1)), (_ms(2022, 1, 1), _ms(2026, 9, 1)))
FORWARD_FROM = _ms(2026, 9, 1)


def load() -> tuple[dict[str, dict[int, DailyBar]], dict[str, list[tuple[int, float]]]]:
    bars: dict[str, dict[int, DailyBar]] = {}
    funding: dict[str, list[tuple[int, float]]] = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for symbol in SYMBOLS:
            bars[symbol] = {
                int(ms): DailyBar(float(h), float(c))
                for ms, h, c in conn.execute(
                    "SELECT open_ms, high, close FROM candles WHERE exchange = 'binance_spot'"
                    " AND symbol = ? AND timeframe = '1d'",
                    (symbol,),
                )
            }
            funding[symbol] = [
                (int(ms), float(rate))
                for ms, rate in conn.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                    " AND symbol = ? ORDER BY funding_ms",
                    (symbol,),
                )
            ]
    return bars, funding


def stats(days: list[int], returns: list[float]) -> dict[str, Any]:
    n = len(returns)
    mean = statistics.fmean(returns)
    sd = statistics.stdev(returns)
    se = sd / math.sqrt(n)
    equity = peak = 1.0
    mdd = 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        mdd = max(mdd, 1 - equity / peak)
    years = n / 365
    return {
        "days": n,
        "mean_daily": mean,
        "se_daily": se,
        "lower_2se": mean - 2 * se,
        "annual_sharpe": mean / sd * math.sqrt(365) if sd > 0 else math.nan,
        "annual_vol": sd * math.sqrt(365),
        "cagr": equity ** (1 / years) - 1 if equity > 0 else -1.0,
        "mdd": mdd,
    }


def buy_and_hold(bars: dict[str, dict[int, DailyBar]]) -> dict[str, Any]:
    """보고용: BTC·ETH 50/50 매일 재조정 보유."""
    days = list(range(FROM_MS, TO_MS + 1, _DAY))
    returns = [
        statistics.fmean(bars[s][d].close / bars[s][d - _DAY].close - 1 for s in SYMBOLS)
        for d in days
    ]
    return stats(days, returns)


def describe(result: TrendResult) -> dict[str, Any]:
    halves = [
        stats(
            [d for d in result.days if lo <= d < hi],
            [r for d, r in zip(result.days, result.returns, strict=True) if lo <= d < hi],
        )
        for lo, hi in HALVES
    ]
    return {
        "full": stats(result.days, result.returns),
        "half_2018_2021": halves[0],
        "half_2022_2026": halves[1],
        "long_pnl": result.long_pnl,
        "short_pnl": result.short_pnl,
        "funding": result.funding,
        "costs": result.costs,
        "turnover_per_year": result.traded_notional / INITIAL_EQUITY / (len(result.days) / 365),
        "trades": result.trades,
        "liquidations": result.liquidations,
        "days_long": result.days_long,
        "days_short": result.days_short,
        "final_equity": result.equity[-1],
    }


def judge(report: dict[str, Any], liquidations: int) -> dict[str, Any]:
    full = report["full"]
    criteria = [
        ("T1", full["lower_2se"] > 0, {"lower_2se": full["lower_2se"], "mean": full["mean_daily"]}),
        (
            "T2",
            report["half_2018_2021"]["mean_daily"] > 0
            and report["half_2022_2026"]["mean_daily"] > 0,
            {
                "2018_2021": report["half_2018_2021"]["mean_daily"],
                "2022_2026": report["half_2022_2026"]["mean_daily"],
            },
        ),
        ("SAFETY", liquidations == 0, {"liquidations": liquidations}),
    ]
    return {
        "status": "PASS" if all(p for _, p, _ in criteria) else "REJECT",
        "criteria": [{"name": n, "passed": p, "detail": d} for n, p, d in criteria],
    }


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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
    main = simulate_trend(
        bars,
        funding,
        from_ms=FROM_MS,
        to_ms=TO_MS,
        initial_equity=INITIAL_EQUITY,
        cfg=TrendConfig(),
    )
    long_only = simulate_trend(
        bars,
        funding,
        from_ms=FROM_MS,
        to_ms=TO_MS,
        initial_equity=INITIAL_EQUITY,
        cfg=TrendConfig(long_only=True),
    )
    report = describe(main)
    verdict = judge(report, main.liquidations)
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(TrendConfig()).items()},
        "verdict": verdict,
        "main": report,
        "report_long_only": describe(long_only),
        "report_buy_and_hold_50_50": buy_and_hold(bars),
    }
    _write(path, record)
    print(f"판정: {verdict['status']}")
    for c in verdict["criteria"]:
        print(f"  {c['name']}: {'통과' if c['passed'] else '실패'} {c['detail']}")
    full = report["full"]
    print(
        f"  샤프 {full['annual_sharpe']:.2f} · CAGR {full['cagr']:.3f} · MDD {full['mdd']:.3f} · "
        f"롱 {report['long_pnl']:.0f} 숏 {report['short_pnl']:.0f} funding {report['funding']:.0f} "
        f"비용 {report['costs']:.0f}"
    )
    print(f"기록: {path}")
    return 0


def forward(output: Path) -> int:
    bars, funding = load()
    last = min(max(b) for b in bars.values())
    if last < FORWARD_FROM:
        print("전향 구간 데이터가 아직 없다 (2026-09 이후 현물 1일봉 필요).")
        return 0
    result = simulate_trend(
        bars,
        funding,
        from_ms=FORWARD_FROM,
        to_ms=last,
        initial_equity=INITIAL_EQUITY,
        cfg=TrendConfig(),
    )
    stamp = datetime.fromtimestamp(last / 1000, tz=UTC).strftime("%Y-%m-%d")
    _write(output / "forward" / f"{stamp}.json", {"report": stats(result.days, result.returns)})
    print(f"전향 보고: {stamp}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("run", "forward"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return run(args.output) if args.stage == "run" else forward(args.output)


if __name__ == "__main__":
    sys.exit(main())
