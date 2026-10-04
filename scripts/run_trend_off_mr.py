"""문서 126 — 추세가 꺼졌을 때만 단기 평균회귀 (추세 + 평균회귀 한 계좌) vs 추세만.

사용 예::

    python -m scripts.run_trend_off_mr run     # → reports/trend_off_mr/result.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_index_mr import COST, _sma, rsi2, stats
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import read_close, trend_signals

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "trend_off_mr"
ENTRIES = (5.0, 10.0)
EXITS = ("sma5", "rsi70")
TRAIN = ("1987-01-01", "2007-12-31")
TEST = ("2008-01-01", "2026-08-31")
EDGE = 0.10
MDD_MARGIN = 0.05
BEARS = (("2000-03-01", "2002-12-31"), ("2007-10-01", "2009-03-31"), ("2022-01-01", "2022-12-31"))


def combined(
    days: Sequence[str],
    closes: Sequence[float],
    trend_on: Sequence[bool],
    entry: float,
    exit_rule: str,
    *,
    use_mr: bool = True,
    same_close: bool = False,
) -> tuple[list[float], list[tuple[int, float]]]:
    """(일간 수익, 평균회귀 매매 [(청산 색인, 수익)]). 신호 t → 실행 t+1 종가."""
    r = rsi2(closes)
    s5 = _sma(closes, 5)
    mr = [False] * len(closes)
    state = False
    for t in range(len(closes)):
        if trend_on[t] or not use_mr or r[t] is None or s5[t] is None:
            state = False
        elif not state:
            state = r[t] < entry  # type: ignore[operator]
        else:
            done = closes[t] > s5[t] if exit_rule == "sma5" else r[t] > 70  # type: ignore[operator]
            state = not done
        mr[t] = state
    lag = 0 if same_close else 1
    daily = [0.0] * len(closes)
    trades: list[tuple[int, float]] = []
    pos = from_mr = False
    entry_px = 0.0
    for t in range(1, len(closes)):
        k = t - lag
        want_trend = trend_on[k] if k >= 0 else False
        want_mr = mr[k] if k >= 0 else False
        target = want_trend or want_mr
        ret = closes[t] / closes[t - 1] - 1 if pos else 0.0
        if target != pos:
            ret -= COST
            if target:
                entry_px = closes[t]
                from_mr = want_mr and not want_trend
            elif from_mr:
                trades.append((t, closes[t] / entry_px * (1 - COST) ** 2 - 1))
            pos = target
        elif pos and want_trend:
            from_mr = False  # 평균회귀로 산 것이 추세 다리로 넘어갔다
        daily[t] = ret
    return daily, trades


def _prep(closes_map: Mapping[str, float], hi: str) -> tuple[list[str], list[float], list[bool]]:
    days = [d for d in sorted(closes_map) if d <= hi]
    xs = [closes_map[d] for d in days]
    sig = trend_signals(days, dict(zip(days, xs, strict=True)))
    return days, xs, [bool(sig.get(d)) for d in days]


def _eval(
    closes_map: Mapping[str, float], lo: str, hi: str, entry: float, ex: str, **kw: Any
) -> dict[str, Any]:
    days, xs, on = _prep(closes_map, hi)
    k = next(i for i, d in enumerate(days) if d >= lo)
    daily, trades = combined(days, xs, on, entry, ex, **kw)
    tr = [(i, v) for i, v in trades if i >= k]
    vals = [v for _, v in tr]
    bears = {}
    for a, b in BEARS:
        sel = [v for i, v in tr if a <= days[i] <= b]
        if sel:
            bears[f"{a[:7]}~{b[:7]}"] = {
                "trades": len(sel),
                "sum": round(sum(sel), 4),
                "win": round(sum(v > 0 for v in sel) / len(sel), 3),
            }
    return stats(daily[k:]) | {
        "mr_trades": len(vals),
        "mr_avg": round(statistics.fmean(vals), 5) if vals else 0.0,
        "mr_win": round(sum(v > 0 for v in vals) / len(vals), 3) if vals else 0.0,
        "bears": bears,
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    cands = [(e, x) for e in ENTRIES for x in EXITS]
    base_train = _eval(ndx, *TRAIN, 10.0, "sma5", use_mr=False)
    train = {f"E{e:g}_{x}": _eval(ndx, *TRAIN, e, x) for e, x in cands}
    best = sorted(train, key=lambda k: (-train[k]["sharpe"], -train[k]["cagr"]))[0]
    edge = train[best]["sharpe"] - base_train["sharpe"]
    result: dict[str, Any] = {
        "train_trend_only": base_train,
        "train": train,
        "chosen": best,
        "train_edge_sharpe": round(edge, 3),
    }
    if edge < EDGE:
        result["verdict"] = "REJECT"
        result["reason"] = f"학습 문턱 미달 ({edge:.3f} < {EDGE})"
    else:
        e = float(best.split("_")[0][1:])
        x = best.split("_", 1)[1]
        qqq, spy = read_close("QQQ_adj"), read_close("SPY_adj")
        tq = _eval(qqq, *TEST, e, x)
        bq = _eval(qqq, *TEST, e, x, use_mr=False)
        ts = _eval(spy, *TEST, e, x)
        bs = _eval(spy, *TEST, e, x, use_mr=False)
        t1 = tq["sharpe"] >= bq["sharpe"] + EDGE
        t2 = tq["cagr"] > bq["cagr"]
        t3 = tq["mdd"] <= bq["mdd"] + MDD_MARGIN
        t4 = ts["sharpe"] >= bs["sharpe"]
        result |= {
            "test_QQQ": tq,
            "test_QQQ_trend_only": bq,
            "test_SPY": ts,
            "test_SPY_trend_only": bs,
            "T1": t1,
            "T2": t2,
            "T3": t3,
            "T4": t4,
            "verdict": "PASS" if t1 and t2 and t3 and t4 else "REJECT",
            "same_close_QQQ_report_only": _eval(qqq, *TEST, e, x, same_close=True),
        }
    result["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_trend_off_mr run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
