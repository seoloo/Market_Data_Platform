"""O/v1 T/v2 신호 1.5배 선물 추가 노출을 사전등록(docs/research/24)대로 1회 판정한다.

사용 예::

    python scripts/run_o_overlay.py run    # 2020-03-01 ~ 2026-08-31 1회 판정

작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_p1 import stats

from auto_trading.backtest.overlay import (
    T2_ALONE,
    OverlayBar,
    OverlayConfig,
    OverlayResult,
    simulate_overlay,
)

DB = Path("data/trading.db")
OUTPUT = Path("reports/o")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INITIAL = 10_000.0
_DAY = 86_400_000


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


FROM_MS = _ms(2020, 3, 1)
"""ETHUSDT funding 자료의 시작. 결측 funding을 0으로 채우지 않는다."""
TO_MS = _ms(2026, 8, 31)
SPLIT_MS = _ms(2023, 1, 1)
MAX_MDD = 0.25
"""T/v2 전향 관찰의 운영 중단선(16 §3)과 같다."""

Bars = dict[str, dict[int, OverlayBar]]


def load() -> tuple[Bars, Bars, dict[str, list[tuple[int, float]]]]:
    spot: Bars = {}
    perp: Bars = {}
    funding: dict[str, list[tuple[int, float]]] = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for s in SYMBOLS:
            for exchange, out in (("binance_spot", spot), ("binance_usdm", perp)):
                out[s] = {
                    int(ms): OverlayBar(float(lo), float(c))
                    for ms, lo, c in conn.execute(
                        "SELECT open_ms, low, close FROM candles WHERE exchange = ?"
                        " AND symbol = ? AND timeframe = '1d' AND open_ms <= ?",
                        (exchange, s, TO_MS),
                    )
                }
            funding[s] = [
                (int(ms), float(r))
                for ms, r in conn.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                    " AND symbol = ? AND funding_ms < ? ORDER BY funding_ms",
                    (s, TO_MS + _DAY),
                )
            ]
    return spot, perp, funding


def _halves(result: OverlayResult) -> tuple[list[float], list[float]]:
    pairs = list(zip(result.days, result.returns, strict=True))
    return [r for d, r in pairs if d < SPLIT_MS], [r for d, r in pairs if d >= SPLIT_MS]


def describe(result: OverlayResult) -> dict[str, Any]:
    first, second = _halves(result)
    yearly: dict[str, float] = {}
    for d, r in zip(result.days, result.returns, strict=True):
        y = str(datetime.fromtimestamp(d / 1000, tz=UTC).year)
        yearly[y] = (1 + yearly.get(y, 0.0)) * (1 + r) - 1
    return {
        "full": stats(result.returns),
        "first_half": stats(first),
        "second_half": stats(second),
        "yearly": yearly,
        "spot_pnl": result.spot_pnl,
        "futures_pnl": result.futures_pnl,
        "funding": result.funding,
        "costs": result.costs,
        "trades": result.trades,
        "liquidations": result.liquidations,
        "halted_day": result.halted_day,
        "loss_limit_days": result.loss_limit_days,
        "capped_days": result.capped_days,
        "deleverage_days": result.deleverage_days,
        "max_futures_leverage": result.max_futures_leverage,
        "worst_intraday_drawdown": result.worst_intraday_drawdown,
        "mean_exposure": statistics.fmean(result.exposure),
        "max_exposure": max(result.exposure),
        "final_equity": result.equity[-1],
    }


def judge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    criteria = [
        ("O1", b["halted_day"] is None, {"halted_day": b["halted_day"]}),
        ("O2", b["liquidations"] == 0, {"liquidations": b["liquidations"]}),
        ("O3", b["full"]["mdd"] <= MAX_MDD, {"mdd": b["full"]["mdd"], "threshold": MAX_MDD}),
        (
            "O4",
            b["full"]["cagr"] > a["full"]["cagr"],
            {"b": b["full"]["cagr"], "a": a["full"]["cagr"]},
        ),
        (
            "O5",
            b["first_half"]["cagr"] > a["first_half"]["cagr"]
            and b["second_half"]["cagr"] > a["second_half"]["cagr"],
            {
                "2020_2022": [b["first_half"]["cagr"], a["first_half"]["cagr"]],
                "2023_2026": [b["second_half"]["cagr"], a["second_half"]["cagr"]],
            },
        ),
    ]
    return {
        "status": "PASS" if all(p for _, p, _ in criteria) else "REJECT",
        "criteria": [{"name": n, "passed": p, "detail": d} for n, p, d in criteria],
    }


def report_only(a: OverlayResult, b: OverlayResult, spot: Bars) -> dict[str, Any]:
    """보고용: 증분(B − A), 고정 수량 50/50 보유, 매일 재조정 50/50 보유."""
    increment = [rb - ra for ra, rb in zip(a.returns, b.returns, strict=True)]
    days = a.days
    first = {s: spot[s][days[0] - _DAY].close for s in SYMBOLS}
    held = [sum(0.5 * spot[s][d].close / first[s] for s in SYMBOLS) for d in days]
    fixed = [held[0] - 1] + [held[k] / held[k - 1] - 1 for k in range(1, len(held))]
    daily = [
        statistics.fmean(spot[s][d].close / spot[s][d - _DAY].close - 1 for s in SYMBOLS)
        for d in days
    ]
    return {
        "increment": stats(increment),
        "corr_a_b": statistics.correlation(a.returns, b.returns),
        "hold_fixed_qty_50_50": stats(fixed),
        "hold_daily_rebalanced_50_50_no_cost": stats(daily),
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
    spot, perp, funding = load()
    kwargs: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    a = simulate_overlay(spot, perp, funding, cfg=T2_ALONE, **kwargs)
    b = simulate_overlay(spot, perp, funding, cfg=OverlayConfig(), **kwargs)
    ra, rb = describe(a), describe(b)
    verdict = judge(ra, rb)
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(OverlayConfig()).items()},
        "verdict": verdict,
        "a_t2_alone": ra,
        "b_overlay": rb,
        "report": report_only(a, b, spot),
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
