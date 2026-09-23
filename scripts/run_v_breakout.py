"""V/v1 변동성 돌파를 사전등록(docs/research/18)대로 1회 판정한다.

사용 예::

    python scripts/run_v_breakout.py run

작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

from auto_trading.backtest.vbreak import BreakoutTrade, DayBar, breakout_trades

DB = Path("data/trading.db")
OUTPUT = Path("reports/v")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
K = 0.5
COST_BPS = 24.0
_DAY = 86_400_000


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


LAST_MS = _ms(2026, 8, 31)
SPLIT_MS = _ms(2022, 1, 1)


def load() -> dict[str, list[DayBar]]:
    out = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for s in SYMBOLS:
            out[s] = [
                DayBar(int(ms), float(o), float(h), float(lo), float(c))
                for ms, o, h, lo, c in conn.execute(
                    "SELECT open_ms, open, high, low, close FROM candles"
                    " WHERE exchange = 'binance_spot' AND symbol = ? AND timeframe = '1d'"
                    " AND open_ms <= ? ORDER BY open_ms",
                    (s, LAST_MS),
                )
            ]
    return out


def stats(values: list[float]) -> dict[str, float | int | None]:
    n = len(values)
    if n < 2:
        return {"n": n, "mean": None, "se": None, "lower_2se": None}
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(n)
    return {"n": n, "mean": mean, "se": se, "lower_2se": mean - 2 * se}


def judge(trades: list[BreakoutTrade]) -> dict[str, Any]:
    full = stats([t.net_bps for t in trades])
    first = stats([t.net_bps for t in trades if t.open_ms < SPLIT_MS])
    second = stats([t.net_bps for t in trades if t.open_ms >= SPLIT_MS])
    criteria = [
        ("V1", (full["lower_2se"] or -1) > 0, {"net_bps": full}),
        (
            "V2",
            (first["mean"] or -1) > 0 and (second["mean"] or -1) > 0,
            {"2017_2021": first, "2022_2026": second},
        ),
    ]
    return {
        "status": "PASS" if all(p for _, p, _ in criteria) else "REJECT",
        "criteria": [{"name": n, "passed": p, "detail": d} for n, p, d in criteria],
    }


def breakdown(trades: list[BreakoutTrade]) -> dict[str, Any]:
    out: dict[str, Any] = {"gross_bps": stats([t.gross_bps for t in trades])}
    for side, name in ((1, "long"), (-1, "short")):
        out[name] = stats([t.net_bps for t in trades if t.side == side])
    return out


def run(output: Path) -> int:
    path = output / "run" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    data = load()
    trades: list[BreakoutTrade] = []
    per_symbol: dict[str, Any] = {}
    ambiguous: dict[str, int] = {}
    for s in SYMBOLS:
        ts, amb = breakout_trades(data[s], k=K, cost_bps=COST_BPS)
        trades += ts
        ambiguous[s] = amb
        per_symbol[s] = breakdown(ts)
    verdict = judge(trades)
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "k": K,
        "cost_bps": COST_BPS,
        "verdict": verdict,
        "all": breakdown(trades),
        "per_symbol": per_symbol,
        "ambiguous_days": ambiguous,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n",
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
    args = parser.parse_args()
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
