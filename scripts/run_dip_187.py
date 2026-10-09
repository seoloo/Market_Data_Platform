"""문서 187 — 코인에만 해 본 지표를 나스닥에 · 고점 −20 · 30% 이상에서 지표 신호로 조기 재매수.

사용 예::

    python -m scripts.run_dip_187 fetch   # Yahoo ^NDX 일봉 → data/research/brake_187/
    python -m scripts.run_dip_187 run     # 1 회 판정 → reports/dip_187/result.json

* 사전 등록: docs/research/187-coin-indicators-on-nasdaq-dip-buy-preregistration.md.
* A 들고/쉬기(46 지표) · B 사건 뒤 20 일(다이버전스 · 피보나치) · C 184 M3 틀 조기 재매수.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_brake_183 import CRISES, _rf_daily, data_real, data_synth, signals
from scripts.run_brake_184 import qqq_real, qqq_synth, simulate, summarize
from scripts.run_lev_div_181 import FEES, NDX_DIV, total_return_index

from auto_trading.backtest.ema_box import Bars
from auto_trading.backtest.indicator_signals import INDICATOR_RULES, direction_series, macd, rsi
from auto_trading.backtest.mj_divergence import SignalConfig, pivots
from auto_trading.backtest.mj_divergence import signals as div_signals

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "brake_187"
OUT = REPO / "reports" / "dip_187"
END = "2026-08-31"
COST = 0.0015
FLOOR = -0.99
HALF = "2006-01-01"
P_MDD = 0.05
FWD = 20
B_MIN_DIFF, B_ALPHA, B_MIN_N = 0.005, 0.05, 30
GATES = (0.20, 0.30)
HIGH_DAYS = 252


# ------------------------------------------------------------------ 자료


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX"
        "?period1=-1325635200&period2=1791331200&interval=1d"
    )
    res = httpx.get(url, timeout=60.0, headers={"User-Agent": ua}).json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    with (DATA / "NDX.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close"])
        for t, o, h, lo, c in zip(
            res["timestamp"], q["open"], q["high"], q["low"], q["close"], strict=True
        ):
            if c and h and lo:
                d = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=t)
                w.writerow([d.strftime("%Y-%m-%d"), o or c, h, lo, c])
    print("fetched")


def data_hash() -> str:
    return hashlib.sha256((DATA / "NDX.csv").read_bytes()).hexdigest()


def load() -> dict[str, list[Any]]:
    rows: dict[str, list[float]] = {}
    with (DATA / "NDX.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] <= END:
                rows[r["date"]] = [float(r[k]) for k in ("open", "high", "low", "close")]
    days = sorted(rows)
    return {
        "days": days,
        "o": [rows[d][0] for d in days],
        "h": [max(rows[d][1], rows[d][3]) for d in days],
        "l": [min(rows[d][2], rows[d][3]) for d in days],
        "c": [rows[d][3] for d in days],
    }


# ------------------------------------------------------------------ A 들고 / 쉬기 (순수)


def lev_px(
    days: Sequence[str], tr: Mapping[str, float], rf: Mapping[str, float], k: float
) -> dict[str, float]:
    out = {days[0]: 100.0}
    fee = FEES[3.0] if k == 3 else 0.0
    for a, b in pairwise(days):
        r = tr[b] / tr[a] - 1
        carry = ((k - 1) * rf.get(a, 0.0) / 100 + fee) / 252
        out[b] = out[a] * (1 + max(k * r - carry, FLOOR))
    return out


def timing(
    days: Sequence[str], px: Mapping[str, float], hold: Sequence[bool | None], i0: int
) -> list[float]:
    """전날 신호로 그날 종가에 들어가고 나온다. 계좌 가치 열 (순수)."""
    v, inside, out = 1.0, False, [1.0]
    for i in range(i0 + 1, len(days)):
        if inside:
            v *= px[days[i]] / px[days[i - 1]]
        want = bool(hold[i - 1])
        if want != inside:
            v *= 1 - COST
            inside = want
        out.append(v)
    return out


def seg_stats(days: Sequence[str], vals: Sequence[float], a: str, b: str) -> dict[str, float]:
    pts = [(d, v) for d, v in zip(days, vals, strict=True) if a <= d <= b]
    yrs = (datetime.fromisoformat(pts[-1][0]) - datetime.fromisoformat(pts[0][0])).days / 365.25
    top, mdd = pts[0][1], 0.0
    for _, v in pts:
        top = max(top, v)
        mdd = max(mdd, 1 - v / top)
    return {"cagr": round((pts[-1][1] / pts[0][1]) ** (1 / yrs) - 1, 4), "mdd": round(mdd, 4)}


def part_a(nd: Mapping[str, Any]) -> dict[str, Any]:
    days, c = nd["days"], nd["c"]
    close = dict(zip(days, c, strict=True))
    tr = total_return_index(days, close, NDX_DIV)
    rf = _rf_daily(days)
    px = {1: lev_px(days, tr, rf, 1), 3: lev_px(days, tr, rf, 3)}
    _, trend = signals(days, close)
    rules: dict[str, list[bool | None]] = {"T": [trend[d] for d in days], "BH": [True] * len(days)}
    for r in INDICATOR_RULES:
        sig = direction_series(r, c, nd["h"], nd["l"])
        rules[r.key] = [None if x is None else x == 1 for x in sig]
    i0 = max(next(i for i, x in enumerate(v) if x is not None) for v in rules.values())
    sub = days[i0:]
    out: dict[str, Any] = {"start": sub[0]}
    for k, hold in rules.items():
        row: dict[str, Any] = {}
        for lev in (1, 3):
            vals = timing(days, px[lev], hold, i0)
            row[f"{lev}x"] = {
                "all": seg_stats(sub, vals, sub[0], END),
                "first": seg_stats(sub, vals, sub[0], "2005-12-31"),
                "second": seg_stats(sub, vals, HALF, END),
            }
        row["switches_per_year"] = round(
            sum(1 for a, b in pairwise(hold[i0:]) if bool(a) != bool(b))
            / ((datetime.fromisoformat(END) - datetime.fromisoformat(sub[0])).days / 365.25),
            1,
        )
        row["exposure"] = round(sum(bool(x) for x in hold[i0:]) / len(sub), 3)
        out[k] = row
    for k in [r.key for r in INDICATOR_RULES]:
        ok = all(
            out[k]["3x"][h]["cagr"] >= out["T"]["3x"][h]["cagr"]
            and out[k]["3x"][h]["mdd"] <= out["T"]["3x"][h]["mdd"] + P_MDD
            for h in ("first", "second")
        )
        out[k]["verdict"] = "T_대체_후보" if ok else "T_보다_못함"
    return out


# ------------------------------------------------------------------ B 사건 뒤 20 일 (순수)


def bars(nd: Mapping[str, Any]) -> Bars:
    n = len(nd["c"])
    return Bars(list(range(n)), nd["o"], nd["h"], nd["l"], nd["c"], [0.0] * n)


def div_events(b: Bars) -> list[int]:
    return sorted({s.i for s in div_signals(b, SignalConfig()) if s.side == 1})


def fib_events(b: Bars) -> list[int]:
    """확정된 마지막 저점 → 그 뒤 확정 고점 다리의 0.382 ~ 0.618 되돌림 첫 진입 (순수)."""
    cfg = SignalConfig()
    lows, highs = pivots(b, cfg)
    out: list[int] = []
    low_i: int | None = None
    high_i: int | None = None
    inside_prev = False
    for t in range(len(b.c)):
        p = t - cfg.right  # 오늘 확정되는 피벗
        if p >= 0 and lows[p]:
            low_i, high_i, inside_prev = p, None, False
        new_high = p >= 0 and highs[p] and low_i is not None and p > low_i
        if new_high and (high_i is None or b.h[p] > b.h[high_i]):
            high_i, inside_prev = p, False
        if low_i is None or high_i is None:
            continue
        a, top = b.low[low_i], b.h[high_i]
        if top <= a:
            continue
        r = (top - b.c[t]) / (top - a)
        inside = 0.382 <= r <= 0.618
        if inside and not inside_prev:
            out.append(t)
        inside_prev = inside
    return out


def fwd_returns(c: Sequence[float], idx: Sequence[int], dedupe: bool) -> list[float]:
    out: list[float] = []
    last = -(10**9)
    for t in idx:
        if t + 1 + FWD >= len(c):
            continue
        if dedupe and t - last <= FWD:
            continue
        out.append(math.log(c[t + 1 + FWD] / c[t + 1]))
        last = t
    return out


def welch_one_sided(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    """(t, 한쪽 p) — p 는 정규 근사(표본 ≥ 30 에서 씀)."""
    vx, vy = statistics.variance(x) / len(x), statistics.variance(y) / len(y)
    t = (statistics.fmean(x) - statistics.fmean(y)) / math.sqrt(vx + vy)
    return t, 0.5 * math.erfc(t / math.sqrt(2))


def part_b(nd: Mapping[str, Any]) -> dict[str, Any]:
    b = bars(nd)
    c = nd["c"]
    control = fwd_returns(c, range(len(c)), dedupe=False)
    out: dict[str, Any] = {
        "control_mean": round(statistics.fmean(control), 5),
        "control_n": len(control),
    }
    for k, ev in (("B1_divergence", div_events(b)), ("B2_fibonacci", fib_events(b))):
        x = fwd_returns(c, ev, dedupe=True)
        row: dict[str, Any] = {"events": len(ev), "n": len(x)}
        if len(x) < B_MIN_N:
            row["verdict"] = "판정_불가"
        else:
            t, p = welch_one_sided(x, control)
            diff = statistics.fmean(x) - statistics.fmean(control)
            row.update(
                mean=round(statistics.fmean(x), 5),
                diff=round(diff, 5),
                t=round(t, 2),
                p=round(p, 4),
            )
            row["verdict"] = "우위_후보" if diff > B_MIN_DIFF and p < B_ALPHA else "증거_없음"
        out[k] = row
    return out


# ------------------------------------------------------------------ C 조기 재매수 (순수)


def bottom_events(nd: Mapping[str, Any]) -> dict[str, set[int]]:
    c = nd["c"]
    r = rsi(c, 14)
    line, sig = macd(c)
    out: dict[str, set[int]] = {
        "S1": set(),
        "S2": set(),
        "S3": set(),
        "S4": set(div_events(bars(nd))),
    }
    for i in range(1, len(c)):
        r0, r1 = r[i - 1], r[i]
        if r0 is not None and r1 is not None and r0 <= 30 < r1:
            out["S1"].add(i)
        a0, s0, a1, s1 = line[i - 1], sig[i - 1], line[i], sig[i]
        if (
            a0 is not None
            and s0 is not None
            and a1 is not None
            and s1 is not None
            and a1 < 0
            and a0 <= s0
            and a1 > s1
        ):
            out["S2"].add(i)
        if i >= 20:
            w0 = c[i - 20 : i]
            w1 = c[i - 19 : i + 1]
            lo0 = statistics.fmean(w0) - 2 * statistics.pstdev(w0)
            lo1 = statistics.fmean(w1) - 2 * statistics.pstdev(w1)
            if c[i - 1] < lo0 and c[i] >= lo1:
                out["S3"].add(i)
    return out


def early_maps(nd: Mapping[str, Any]) -> dict[str, dict[str, bool]]:
    days, c = nd["days"], nd["c"]
    ev = bottom_events(nd)
    out: dict[str, dict[str, bool]] = {}
    for g in GATES:
        gate = [c[i] <= (1 - g) * max(c[max(0, i - HIGH_DAYS + 1) : i + 1]) for i in range(len(c))]
        for s, idx in ev.items():
            out[f"g{int(g * 100)}_{s}"] = {days[i]: True for i in idx if gate[i]}
    return out


def part_c(nd: Mapping[str, Any]) -> dict[str, Any]:
    maps = early_maps(nd)
    res: dict[str, Any] = {}
    for name, data, q in (
        ("R", data_real(), None),
        ("S", (ds := data_synth()), qqq_synth(ds["days"], ds["rf"])),
    ):
        qq = qqq_real() if q is None else q
        row: dict[str, Any] = {
            "T0": summarize(simulate(data, qq, ref="T", park="cash"), CRISES[name])
        }
        for k, m in maps.items():
            row[k] = summarize(simulate(data, qq, ref="T", park="cash", early=m), CRISES[name])
        res[name] = row
    verdict: dict[str, Any] = {}
    for k in maps:
        ok = all(
            res[s][k]["irr_liq"] > res[s]["T0"]["irr_liq"]
            and res[s][k]["mdd"] <= res[s]["T0"]["mdd"] + P_MDD
            for s in ("R", "S")
        )
        verdict[k] = {
            "irr_diff_[R,S]": [
                round(res[s][k]["irr_liq"] - res[s]["T0"]["irr_liq"], 4) for s in ("R", "S")
            ],
            "pass": ok,
        }
    passed = [k for k in maps if verdict[k]["pass"]]
    verdict["choice"] = (
        max(passed, key=lambda k: sum(verdict[k]["irr_diff_[R,S]"])) if passed else "T0"
    )
    res["verdict"] = verdict
    return res


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    nd = load()
    res = {
        "data_sha256": data_hash(),
        "ndx_range": [nd["days"][0], nd["days"][-1]],
        "A": part_a(nd),
        "B": part_b(nd),
        "C": part_c(nd),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv == ["fetch"]:
        fetch()
        return 0
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_dip_187 fetch|run")
    res = run()
    a = res["A"]
    sys.stdout.write(f"A start {a['start']}\n")
    for k in ("BH", "T", *[r.key for r in INDICATOR_RULES]):
        r = a[k]
        cells = " | ".join(
            f"{lev} {r[lev]['all']['cagr'] * 100:5.1f}/{r[lev]['all']['mdd'] * 100:3.0f} "
            f"앞 {r[lev]['first']['cagr'] * 100:5.1f}/{r[lev]['first']['mdd'] * 100:3.0f} "
            f"뒤 {r[lev]['second']['cagr'] * 100:5.1f}/{r[lev]['second']['mdd'] * 100:3.0f}"
            for lev in ("1x", "3x")
        )
        tail = f"전환/년 {r['switches_per_year']} 노출 {r['exposure']} {r.get('verdict', '')}"
        sys.stdout.write(f"{k:3s} {cells} {tail}\n")
    sys.stdout.write(json.dumps(res["B"], ensure_ascii=False) + "\n")
    cc = res["C"]
    for s in ("R", "S"):
        for k, r in cc[s].items():
            sys.stdout.write(
                f"C {s} {k:9s} irr {r['irr_liq'] * 100:5.1f} mdd {r['mdd'] * 100:4.1f} "
                f"매도 {r['brake_sales']} 재매수 {r['rebuys']} 손해 {r['loss_rebuy_share']}\n"
            )
    sys.stdout.write(json.dumps(cc["verdict"], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
