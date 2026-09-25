"""F1b 빠른 추세 롱숏 무기한 (docs/research/45).

사용 예::

    python -m scripts.run_f1 check   # 자료 점검만 (수익 미계산)
    python -m scripts.run_f1 run     # 사용자 승인 뒤 1회 판정

``run`` 은 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import zipfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_o_overlay import load as load_overlay
from scripts.run_t_trend import stats

from auto_trading.backtest.fast_trend import F1B, FastBar, FastTrendResult, simulate_fast_trend
from auto_trading.backtest.overlay import T2_ALONE, simulate_overlay
from auto_trading.backtest.trend import REALISTIC_PERP_COST

DB = Path("data/trading.db")
MARK_DIR = Path("data/external/binance_vision/um_mark")
OUTPUT = Path("reports/f1")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INITIAL = 10_000.0
_DAY = 86_400_000


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


FROM_MS = _ms(2020, 3, 1)
TO_MS = _ms(2026, 8, 31)
HALVES = ((FROM_MS, _ms(2023, 1, 1)), (_ms(2023, 1, 1), TO_MS + _DAY))


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _mark() -> dict[str, dict[int, tuple[float, float]]]:
    out: dict[str, dict[int, tuple[float, float]]] = {}
    for s in SYMBOLS:
        rows: dict[int, tuple[float, float]] = {}
        for path in sorted((MARK_DIR / s / "1d").glob(f"{s}-1d-*.zip")):
            with zipfile.ZipFile(path) as archive:
                (name,) = archive.namelist()
                for line in archive.read(name).decode("ascii").splitlines():
                    cells = line.split(",")
                    if cells[0].isdigit():
                        rows[int(cells[0])] = (float(cells[2]), float(cells[3]))
        out[s] = rows
    return out


def load() -> tuple[
    dict[str, dict[int, float]],
    dict[str, dict[int, FastBar]],
    dict[str, list[tuple[int, float]]],
    dict[str, list[str]],
]:
    """신호(현물 종가)·무기한 봉(mark 결측일은 현물 고저로 대체)·funding·대체한 날."""
    mark = _mark()
    signal: dict[str, dict[int, float]] = {}
    bars: dict[str, dict[int, FastBar]] = {}
    funding: dict[str, list[tuple[int, float]]] = {}
    filled: dict[str, list[str]] = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for s in SYMBOLS:
            spot = {
                int(ms): (float(h), float(lo), float(c))
                for ms, h, lo, c in conn.execute(
                    "SELECT open_ms, high, low, close FROM candles WHERE exchange = 'binance_spot'"
                    " AND symbol = ? AND timeframe = '1d' AND open_ms <= ?",
                    (s, TO_MS),
                )
            }
            signal[s] = {ms: v[2] for ms, v in spot.items()}
            bars[s] = {}
            filled[s] = []
            for ms, h, lo, c in conn.execute(
                "SELECT open_ms, high, low, close FROM candles WHERE exchange = 'binance_usdm'"
                " AND symbol = ? AND timeframe = '1d' AND open_ms <= ?",
                (s, TO_MS),
            ):
                ms = int(ms)
                if ms in mark[s]:
                    mh, ml = mark[s][ms]
                else:
                    mh, ml = spot[ms][0], spot[ms][1]
                    if FROM_MS - _DAY <= ms <= TO_MS:
                        filled[s].append(_date(ms))
                bars[s][ms] = FastBar(float(h), float(lo), float(c), mh, ml)
            funding[s] = [
                (int(ms), float(r))
                for ms, r in conn.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                    " AND symbol = ? AND funding_ms < ? ORDER BY funding_ms",
                    (s, TO_MS + _DAY),
                )
            ]
    return signal, bars, funding, filled


def check() -> dict[str, Any]:
    signal, bars, funding, filled = load()
    days = range(FROM_MS - _DAY, TO_MS + 1, _DAY)
    return {
        s: {
            "perp_days_missing": [_date(d) for d in days if d not in bars[s]],
            "signal_days_missing": [_date(d) for d in days if d not in signal[s]],
            "mark_filled_with_spot": filled[s],
            "funding_first": _date(funding[s][0][0]),
            "funding_count_in_window": sum(
                1 for ms, _ in funding[s] if FROM_MS <= ms <= TO_MS + _DAY
            ),
        }
        for s in SYMBOLS
    } | {"note": "문서 45 §2 자료 점검. 수익은 계산하지 않았다."}


def _worst_first(result: FastTrendResult) -> dict[str, Any]:
    eq = [INITIAL, *result.equity]
    peak, peak_i, dd, span = eq[0], 0, 0.0, (0, 0)
    for i, e in enumerate(eq):
        if e > peak:
            peak, peak_i = e, i
        if 1 - e / peak > dd:
            dd, span = 1 - e / peak, (peak_i, i)
    days = [FROM_MS - _DAY, *result.days]
    worst_i = min(range(len(result.returns)), key=result.returns.__getitem__)
    return {
        "worst_intraday_drawdown_mark": result.worst_intraday_drawdown,
        "worst_intraday_drawdown_last": result.worst_intraday_drawdown_last,
        "worst_day": [_date(result.days[worst_i]), result.returns[worst_i]],
        "worst_30d": min(eq[i] / eq[i - 30] - 1 for i in range(30, len(eq))),
        "mdd": dd,
        "mdd_peak": _date(days[span[0]]),
        "mdd_trough": _date(days[span[1]]),
        "stop_hits": {"long": result.stop_hits_long, "short": result.stop_hits_short},
        "stop_days": [f"{_date(d)} {s[:3]} {side}" for d, s, side in result.stop_days],
    }


def describe(result: FastTrendResult) -> dict[str, Any]:
    halves = [
        stats(
            [d for d in result.days if lo <= d < hi],
            [r for d, r in zip(result.days, result.returns, strict=True) if lo <= d < hi],
        )
        for lo, hi in HALVES
    ]
    years = len(result.days) / 365
    return {
        "worst_first": _worst_first(result),
        "full": stats(result.days, result.returns),
        "half_2020_2022": halves[0],
        "half_2023_2026": halves[1],
        "long": {
            "price": result.long_pnl,
            "funding": result.long_funding,
            "costs": result.long_costs,
        },
        "short": {
            "price": result.short_pnl,
            "funding": result.short_funding,
            "costs": result.short_costs,
            "net": result.short_net,
        },
        "liquidations": result.liquidations,
        "halted_day": None if result.halted_day is None else _date(result.halted_day),
        "trades": result.trades,
        "turnover_per_year_vs_initial": result.traded_notional / INITIAL / years,
        "days_long": result.days_long,
        "days_short": result.days_short,
        "final_equity": result.equity[-1],
    }


def judge(rep: dict[str, Any]) -> dict[str, Any]:
    criteria = [
        ("F1", rep["full"]["lower_2se"] > 0, {"lower_2se": rep["full"]["lower_2se"]}),
        (
            "F2",
            rep["half_2020_2022"]["mean_daily"] > 0 and rep["half_2023_2026"]["mean_daily"] > 0,
            {
                "2020_2022": rep["half_2020_2022"]["mean_daily"],
                "2023_2026": rep["half_2023_2026"]["mean_daily"],
            },
        ),
        ("F3", rep["short"]["net"] > 0, {"short_net": rep["short"]["net"]}),
        (
            "SAFETY",
            rep["liquidations"] == 0 and rep["halted_day"] is None,
            {"liquidations": rep["liquidations"], "halted_day": rep["halted_day"]},
        ),
    ]
    return {
        "status": "PASS" if all(p for _, p, _ in criteria) else "REJECT",
        "criteria": [{"name": n, "passed": p, "detail": d} for n, p, d in criteria],
    }


def _t2_returns() -> dict[int, float]:
    """보고용: 같은 구간 T/v2 단독 일간 수익."""
    spot, perp, funding = load_overlay()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    result = simulate_overlay(spot, perp, funding, cfg=T2_ALONE, **kw)
    return dict(zip(result.days, result.returns, strict=True))


def run(output: Path) -> int:
    path = output / "run" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    signal, bars, funding, filled = load()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    main = simulate_fast_trend(signal, bars, funding, cfg=F1B, **kw)
    rep = describe(main)
    verdict = judge(rep)
    refs = {
        "cost_reference_0_023pct": replace(F1B, perp_cost=REALISTIC_PERP_COST),
        "stop_on_mark": replace(F1B, stop_on_mark=True),
        "liquidate_on_last_price": replace(F1B, liquidate_on_mark=False),
    }
    reference = {
        name: describe(simulate_fast_trend(signal, bars, funding, cfg=c, **kw))
        for name, c in refs.items()
    }
    t2 = _t2_returns()
    pairs = [(t2[d], r) for d, r in zip(main.days, main.returns, strict=True) if d in t2]
    combo = [(a + b) / 2 for a, b in pairs]
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(F1B).items()},
        "mark_filled_with_spot": filled,
        "verdict": verdict,
        "f1b": rep,
        "reference_only": {
            k: {"full": v["full"], "worst_first": v["worst_first"], "short": v["short"]}
            for k, v in reference.items()
        },
        "with_t2": {
            "daily_return_correlation": statistics.correlation(
                [a for a, _ in pairs], [b for _, b in pairs]
            ),
            "t2_alone": stats(main.days[: len(pairs)], [a for a, _ in pairs]),
            "half_t2_half_f1b": stats(main.days[: len(pairs)], combo),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"판정: {verdict['status']}")
    for c in verdict["criteria"]:
        print(f"  {c['name']}: {'통과' if c['passed'] else '실패'}")
    print(f"  기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "check":
        sys.stdout.write(json.dumps(check(), ensure_ascii=False, indent=1) + "\n")
        return 0
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
