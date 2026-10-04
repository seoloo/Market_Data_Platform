"""문서 125 — 지수 ETF 단기 평균회귀(Connors RSI(2)): 나스닥 100 1987~2007 로 고르고 2008~ 로 검증.

사용 예::

    python -m scripts.run_index_mr run     # → reports/index_mr/result.json
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import read_close, trend_signals

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "index_mr"
COST = 0.001 + 0.0002
ENTRIES = (5.0, 10.0)
EXITS = ("sma5", "rsi70")
TRAIN = ("1987-01-01", "2007-12-31")
TEST = ("2008-01-01", "2026-08-31")
TRAIN_SHARPE, TRAIN_AVG = 0.4, 0.002
T1_SHARPE, T3_SHARPE, T4_EDGE = 0.4, 0.3, 0.10


def rsi2(closes: Sequence[float], n: int = 2) -> list[float | None]:
    """와일더 평활 RSI(n): 첫 평균은 처음 n 개 변화의 평균, 그 뒤 (이전 × (n−1) + 오늘) / n."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    ch = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain = sum(max(c, 0.0) for c in ch[:n]) / n
    loss = sum(max(-c, 0.0) for c in ch[:n]) / n

    def value(g: float, lo: float) -> float:
        return 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)

    out[n] = value(gain, loss)
    for i in range(n + 1, len(closes)):
        c = ch[i - 1]
        gain = (gain * (n - 1) + max(c, 0.0)) / n
        loss = (loss * (n - 1) + max(-c, 0.0)) / n
        out[i] = value(gain, loss)
    return out


def _sma(xs: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def mr_returns(
    closes: Sequence[float], entry: float, exit_rule: str, *, same_close: bool = False
) -> tuple[list[float], list[tuple[int, float]], int]:
    """(일간 수익, [(청산한 날 색인, 매매 수익)], 들고 있던 날 수).

    신호 t → 실행 t+1 종가 (same_close 면 t 종가).
    """
    r = rsi2(closes)
    s200, s5 = _sma(closes, 200), _sma(closes, 5)
    lag = 0 if same_close else 1
    daily = [0.0] * len(closes)
    trades: list[tuple[int, float]] = []
    holding = False
    want = [False] * len(closes)
    state = False
    for t in range(len(closes)):
        if r[t] is None or s200[t] is None or s5[t] is None:
            want[t] = state
            continue
        if not state:
            if closes[t] > s200[t] and r[t] < entry:  # type: ignore[operator]
                state = True
        else:
            done = closes[t] > s5[t] if exit_rule == "sma5" else r[t] > 70  # type: ignore[operator]
            if done:
                state = False
        want[t] = state
    entry_px = 0.0
    held = 0
    for t in range(1, len(closes)):
        pos_prev = holding
        target = want[t - lag] if t - lag >= 0 else False
        ret = closes[t] / closes[t - 1] - 1 if pos_prev else 0.0
        if target != pos_prev:
            ret -= COST
            if target:
                entry_px = closes[t]
            else:
                trades.append((t, closes[t] / entry_px * (1 - COST) ** 2 - 1))
            holding = target
        daily[t] = ret
        held += int(pos_prev)
    return daily, trades, held


def trend_returns(days: Sequence[str], closes: Sequence[float]) -> list[float]:
    sig = trend_signals(list(days), dict(zip(days, closes, strict=True)))
    daily = [0.0] * len(closes)
    pos = False
    for t in range(1, len(closes)):
        target = bool(sig.get(days[t - 1]))
        ret = closes[t] / closes[t - 1] - 1 if pos else 0.0
        if target != pos:
            ret -= COST
            pos = target
        daily[t] = ret
    return daily


def stats(daily: Sequence[float]) -> dict[str, float]:
    eq = 1.0
    peak = mdd = 0.0
    for d in daily:
        eq *= 1 + d
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    years = len(daily) / 252
    sd = statistics.stdev(daily) if len(daily) > 1 else 0.0
    return {
        "cagr": round(eq ** (1 / years) - 1, 4) if years > 0 else 0.0,
        "sharpe": round(statistics.fmean(daily) / sd * math.sqrt(252), 3) if sd > 0 else 0.0,
        "mdd": round(mdd, 4),
    }


def _seg(
    days: Sequence[str], closes: Mapping[str, float], lo: str, hi: str
) -> tuple[list[str], list[float]]:
    ds = [d for d in days if d <= hi]
    xs = [closes[d] for d in ds]
    first = next(i for i, d in enumerate(ds) if d >= lo)
    return ds[first:], xs  # 이력은 앞에서부터 다 쓰고, 통계는 lo 부터


def _run_one(
    days: Sequence[str],
    closes: Mapping[str, float],
    lo: str,
    hi: str,
    entry: float,
    ex: str,
    *,
    same_close: bool = False,
) -> dict[str, Any]:
    span, xs = _seg(days, closes, lo, hi)
    daily, trades, _held = mr_returns(xs, entry, ex, same_close=same_close)
    k = len(xs) - len(span)
    tr = [r for i, r in trades if i >= k]  # 판정 구간 안에서 끝난 매매만
    seg_daily = daily[k:]
    out: dict[str, Any] = stats(seg_daily) | {
        "trades": len(tr),
        "avg_trade": round(statistics.fmean(tr), 5) if tr else 0.0,
        "win": round(sum(t > 0 for t in tr) / len(tr), 3) if tr else 0.0,
        "time_in": round(sum(1 for d in seg_daily if d != 0.0) / len(seg_daily), 3),
    }
    out["_daily"] = seg_daily
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    nd = sorted(ndx)
    cands = [(e, x) for e in ENTRIES for x in EXITS]
    train = {f"E{e:g}_{x}": _run_one(nd, ndx, *TRAIN, e, x) for e, x in cands}
    best = sorted(train, key=lambda k: (-train[k]["sharpe"], -train[k]["avg_trade"]))[0]
    clean = {k: {kk: vv for kk, vv in v.items() if kk != "_daily"} for k, v in train.items()}
    result: dict[str, Any] = {"train": clean, "chosen": best}
    tb = train[best]
    if tb["sharpe"] < TRAIN_SHARPE or tb["avg_trade"] < TRAIN_AVG:
        result["verdict"] = "REJECT"
        result["reason"] = (
            f"학습 문턱 미달 (샤프 {tb['sharpe']} / 매매당 {tb['avg_trade']:.4%}"
            f" vs {TRAIN_SHARPE} / {TRAIN_AVG:.2%})"
        )
    else:
        e = float(best.split("_")[0][1:])
        x = best.split("_", 1)[1]
        qqq = read_close("QQQ_adj")
        spy = read_close("SPY_adj")
        qd, sd_ = sorted(qqq), sorted(spy)
        tq = _run_one(qd, qqq, *TEST, e, x)
        ts = _run_one(sd_, spy, *TEST, e, x)
        span, xs = _seg(qd, qqq, *TEST)
        trend = trend_returns([d for d in qd if d <= TEST[1]], xs)[len(xs) - len(span) :]
        mix = [(a + b) / 2 for a, b in zip(tq["_daily"], trend, strict=True)]
        t_stats, m_stats = stats(trend), stats(mix)
        corr = statistics.correlation(tq["_daily"], trend)
        t1 = tq["sharpe"] >= T1_SHARPE
        t2 = tq["cagr"] > 0 and tq["avg_trade"] > 0
        t3 = ts["sharpe"] >= T3_SHARPE
        t4 = m_stats["sharpe"] >= t_stats["sharpe"] + T4_EDGE
        result |= {
            "test_QQQ": {k: v for k, v in tq.items() if k != "_daily"},
            "test_SPY": {k: v for k, v in ts.items() if k != "_daily"},
            "trend_QQQ": t_stats,
            "mix_half": m_stats,
            "corr_mr_trend": round(corr, 3),
            "T1": t1,
            "T2": t2,
            "T3": t3,
            "T4": t4,
            "verdict": "PASS" if t1 and t2 and t3 and t4 else "REJECT",
            "same_close_QQQ_report_only": {
                k: v
                for k, v in _run_one(qd, qqq, *TEST, e, x, same_close=True).items()
                if k != "_daily"
            },
        }
    result["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_index_mr run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["mr_returns", "rsi2"]
