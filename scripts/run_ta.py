"""TA/v1 다종목 선물 추세를 사전등록(docs/research/20)대로 1회 판정한다.

사용 예::

    python scripts/run_ta.py coverage   # 유니버스 종목-일의 일봉·funding 적재율 (수익 없음)
    python scripts/run_ta.py run        # 2020-06-01 ~ 2026-08-31 1회 판정

run은 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import hashlib
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

from auto_trading.backtest.multi_trend import (
    DayOHLC,
    MultiTrendConfig,
    MultiTrendResult,
    simulate_multi_trend,
)

DB = Path("data/trading.db")
UNIVERSE = Path("reports/ta/universe.json")
UNIVERSE_SHA256 = "4ef00c4b800cf80f9f646327a8215b7921fe69de9df3e9fb65a29a77d0e5d1fd"
OUTPUT = Path("reports/ta")
INITIAL = 10_000.0
_DAY = 86_400_000


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


FROM_MS = _ms(2020, 6, 1)
TO_MS = _ms(2026, 8, 31)
SPLIT_MS = _ms(2023, 7, 1)
MAX_MDD = 0.30

Bars = dict[str, dict[int, DayOHLC]]
Funding = dict[str, list[tuple[int, float]]]


def load_universe() -> dict[int, list[str]]:
    text = UNIVERSE.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.rstrip("\n").encode()).hexdigest()
    if digest != UNIVERSE_SHA256:
        raise SystemExit(f"유니버스 파일이 사전등록 값과 다르다: {digest}")
    return {int(k): v for k, v in json.loads(text).items()}


def load(universe: dict[int, list[str]]) -> tuple[Bars, Funding]:
    symbols = sorted({s for v in universe.values() for s in v})
    bars: Bars = {}
    funding: Funding = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for s in symbols:
            bars[s] = {
                int(ms): DayOHLC(float(h), float(lo), float(c))
                for ms, h, lo, c in conn.execute(
                    "SELECT open_ms, high, low, close FROM candles WHERE exchange = 'binance_usdm'"
                    " AND symbol = ? AND timeframe = '1d'",
                    (s,),
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


def _month_start(ms: int) -> int:
    moment = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return _ms(moment.year, moment.month, 1)


def coverage(universe: dict[int, list[str]], bars: Bars, funding: Funding) -> dict[str, float]:
    """유니버스 종목-일 중 일봉이 있는 비율과 그날 funding 정산이 있는 비율."""
    funded = {s: {ms - ms % _DAY for ms, _ in v} for s, v in funding.items()}
    need = have_bar = have_funding = 0
    for day in range(FROM_MS, TO_MS + 1, _DAY):
        for s in universe.get(_month_start(day), ()):
            need += 1
            have_bar += day in bars.get(s, {})
            have_funding += day in funded.get(s, set())
    return {
        "symbol_days": need,
        "bar_share": have_bar / need,
        "funding_share": have_funding / need,
    }


def stats(days: list[int], returns: list[float]) -> dict[str, float | int]:
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
        "se_daily": sd / math.sqrt(n),
        "lower_2se": mean - 2 * sd / math.sqrt(n),
        "annual_sharpe": mean / sd * math.sqrt(365) if sd > 0 else math.nan,
        "annual_vol": sd * math.sqrt(365),
        "cagr": equity ** (365 / n) - 1 if equity > 0 else -1.0,
        "mdd": mdd,
    }


def describe(result: MultiTrendResult) -> dict[str, Any]:
    pairs = list(zip(result.days, result.returns, strict=True))
    first = [r for d, r in pairs if d < SPLIT_MS]
    second = [r for d, r in pairs if d >= SPLIT_MS]
    yearly: dict[str, float] = {}
    for d, r in pairs:
        y = str(datetime.fromtimestamp(d / 1000, tz=UTC).year)
        yearly[y] = (1 + yearly.get(y, 0.0)) * (1 + r) - 1
    return {
        "full": stats(result.days, result.returns),
        "first_half": stats([], first) if len(first) > 1 else {},
        "second_half": stats([], second) if len(second) > 1 else {},
        "yearly": yearly,
        "long_pnl": result.long_pnl,
        "short_pnl": result.short_pnl,
        "funding": result.funding,
        "costs": result.costs,
        "trades": result.trades,
        "liquidations": result.liquidations,
        "forced_closes": result.forced_closes,
        "mean_gross": statistics.fmean(result.gross),
        "max_gross": max(result.gross),
        "symbols_traded": len(result.symbols_traded),
        "final_equity": result.equity[-1],
    }


def judge(report: dict[str, Any], liquidations: int) -> dict[str, Any]:
    full = report["full"]
    criteria = [
        ("A1", full["lower_2se"] > 0, {"lower_2se": full["lower_2se"], "mean": full["mean_daily"]}),
        (
            "A2",
            report["first_half"].get("mean_daily", -1) > 0
            and report["second_half"].get("mean_daily", -1) > 0,
            {
                "2020_2023": report["first_half"].get("mean_daily"),
                "2023_2026": report["second_half"].get("mean_daily"),
            },
        ),
        ("A3", full["mdd"] <= MAX_MDD, {"mdd": full["mdd"], "threshold": MAX_MDD}),
        ("SAFETY", liquidations == 0, {"liquidations": liquidations}),
    ]
    return {
        "status": "PASS" if all(p for _, p, _ in criteria) else "REJECT",
        "criteria": [{"name": n, "passed": p, "detail": d} for n, p, d in criteria],
    }


def _replay(
    universe: dict[int, list[str]], bars: Bars, funding: Funding, cfg: MultiTrendConfig
) -> MultiTrendResult:
    return simulate_multi_trend(
        bars, funding, universe, from_ms=FROM_MS, to_ms=TO_MS, initial_equity=INITIAL, cfg=cfg
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
    universe = load_universe()
    bars, funding = load(universe)
    main = _replay(universe, bars, funding, MultiTrendConfig())
    majors = {k: ["BTCUSDT", "ETHUSDT"] for k in universe}
    majors_only = _replay(majors, bars, funding, MultiTrendConfig(universe_size=2))
    report = describe(main)
    verdict = judge(report, main.liquidations)
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(MultiTrendConfig()).items()},
        "universe_sha256": UNIVERSE_SHA256,
        "verdict": verdict,
        "main": report,
        "report_btc_eth_only_perp": describe(majors_only),
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
    full = report["full"]
    print(
        f"  샤프 {full['annual_sharpe']:.2f} · CAGR {full['cagr']:.3f} · MDD {full['mdd']:.3f} ·"
        f" 평균 총명목 {report['mean_gross']:.2f}"
    )
    print(f"기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("coverage", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "coverage":
        universe = load_universe()
        bars, funding = load(universe)
        print(json.dumps(coverage(universe, bars, funding), indent=1))
        return 0
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
