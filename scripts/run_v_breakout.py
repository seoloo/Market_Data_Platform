"""V 변동성 돌파를 사전등록(docs/research/18)대로 1회 판정한다.

사용 예::

    python scripts/run_v_breakout.py run      # V/v1 (실행됨 — 미래 정보 결함으로 무효, 18 §5)
    python scripts/run_v_breakout.py run_v11  # V/v1.1 결함 수정판 1회 (18 §6)

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

from auto_trading.backtest.vbreak import (
    BreakoutTrade,
    DayBar,
    HourBar,
    breakout_trades,
    resolved_breakout_trades,
)

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


def load_hours() -> dict[str, dict[int, list[HourBar]]]:
    """종목 → 일봉 open_ms → 그날 현물 1시간봉 (V/v1.1 먼저 닿은 쪽 판별)."""
    out: dict[str, dict[int, list[HourBar]]] = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for s in SYMBOLS:
            days: dict[int, list[HourBar]] = {}
            for ms, h, lo in conn.execute(
                "SELECT open_ms, high, low FROM candles WHERE exchange = 'binance_spot'"
                " AND symbol = ? AND timeframe = '1h' AND open_ms < ? ORDER BY open_ms",
                (s, LAST_MS + _DAY),
            ):
                days.setdefault(int(ms) - int(ms) % _DAY, []).append(
                    HourBar(int(ms), float(h), float(lo))
                )
            out[s] = days
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


def run_v11(output: Path) -> int:
    """V/v1.1: 양방향 돌파일을 빼지 않고 1시간봉으로 먼저 닿은 쪽을 가린다 (18 §6)."""
    path = output / "run_v11" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    data = load()
    hours = load_hours()
    trades: list[BreakoutTrade] = []
    per_symbol: dict[str, Any] = {}
    resolution: dict[str, Any] = {}
    for s in SYMBOLS:
        ts, res = resolved_breakout_trades(data[s], hours[s], k=K, cost_bps=COST_BPS)
        trades += ts
        per_symbol[s] = breakdown(ts)
        resolution[s] = {"by_hour": res.by_hour, "worse_of": res.worse_of}
    verdict = judge(trades)
    record = {
        "version": "V/v1.1",
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "k": K,
        "cost_bps": COST_BPS,
        "verdict": verdict,
        "all": breakdown(trades),
        "per_symbol": per_symbol,
        "both_sides_resolution": resolution,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"V/v1.1 판정: {verdict['status']}")
    for c in verdict["criteria"]:
        print(f"  {c['name']}: {'통과' if c['passed'] else '실패'} {c['detail']}")
    print(f"기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("run", "run_v11"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return run(args.output) if args.stage == "run" else run_v11(args.output)


if __name__ == "__main__":
    sys.exit(main())
