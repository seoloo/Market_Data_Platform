"""본편 추가 계산 — calc2_spec.md 그대로 (설명용 회계 · 조건부 재표본, 판정 아님).

    <numpy 있는 파이썬> reports/deneb_polaris_report_20261017/blue_manual/calc2.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from types import FrameType
from typing import Any

import numpy as np

W = Path(__file__).resolve().parent
R = W.parents[2]
sys.path[:0] = [str(R), str(R / "src"), str(W)]
sys.path.append(str(R / ".venv/Lib/site-packages"))
from scripts import run_brake_184 as b  # noqa: E402
from scripts import run_namu_core_compare as c  # noqa: E402

import calc as c1  # noqa: E402  (같은 signals · capture_pol · CASES)

END = "2026-08-31"
M = 200_000
LEDGER = R / "reports/deneb_polaris_report_20261017/full_report/calculation_result.json"
WINDOWS = {
    "2018년 4분기": ("2018-10-01", "2018-12-31"),
    "2020년 코로나": ("2020-02-01", "2020-04-30"),
    "2022년 한 해": ("2022-01-01", "2022-12-31"),
    "2025년 봄": ("2025-02-01", "2025-05-31"),
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def window_dd(days: list[str], nav: list[float], a: str, z: str) -> float:
    xs = [v for d, v in zip(days, nav, strict=True) if a <= d <= z]
    peak, worst = xs[0], 0.0
    for v in xs:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def yearly(days: list[str], nav: list[float]) -> dict[str, float]:
    last: dict[str, float] = {}
    for d, v in zip(days, nav, strict=True):
        last[d[:4]] = v
    out, prev = {}, nav[0]
    for y in sorted(last):
        out[y] = last[y] / prev - 1
        prev = last[y]
    return out


def polaris_weights(px: dict, days: list[str], i0: int, i1: int, pol: str) -> dict[str, list[float]]:
    """run_policy 의 날마다 끝 보유 수량 · 가격 → 달말 주식 · 국채 · 금 비중."""
    rec: dict[str, tuple[dict, dict]] = {}

    def local(f: FrameType, e: str, a: Any):
        if e == "line":
            lo = f.f_locals
            if "d" in lo and "q" in lo and "price" in lo:
                rec[lo["d"]] = (dict(lo["q"]), dict(lo["price"]))
        return local

    def glob(f: FrameType, e: str, a: Any):
        return local if f.f_code is c.run_policy.__code__ else None

    sys.settrace(glob)
    try:
        c.run_policy(px, days, i0, i1, pol, lambda _: 100_000.0)
    finally:
        sys.settrace(None)
    month: dict[str, tuple[dict, dict]] = {}
    for d in sorted(rec):
        month[d[:7]] = rec[d]
    out: dict[str, list] = {"months": [], "stock": [], "bond": [], "gold": []}
    for mth, (q, p) in month.items():
        v = {s: q[s] * p[s] for s in q}
        tot = sum(v.values())
        if tot <= 0:
            continue
        out["months"].append(mth)
        out["stock"].append((v["SPY"] + v["QQQ"]) / tot)
        out["bond"].append(v["IEF"] / tot)
        out["gold"].append(v["GLD"] / tot)
    return out


def save(m: float, y: int, r: float, tax: float = 0.0) -> float:
    z = 0.0
    for _ in range(y):
        z = z + z * r * (1 - tax) + 12 * m + 6.5 * m * r * (1 - tax)
    return z


def main() -> None:
    b.TAX = 0.0
    c.TAX_RATE = 0.0
    c.FEE = 0.0015
    px = {s: c.load(R / f"data/research/div_mix_180/{s}.csv") for s in ["QQQ", "SCHD", "SPY", "IEF", "GLD"]}
    px["TQQQ"] = c.load(R / "data/research/lev_div_181/TQQQ.csv")
    px["QLD"] = c.load(R / "data/research/lev_div_181/QLD.csv")
    common = sorted(d for d in set.intersection(*(set(v) for v in px.values())) if d <= END)
    i0 = next(i for i, d in enumerate(common) if d >= "2012-01-01")
    days = common[i0:]
    vol, tr = c1.signals(common, px["QQQ"])

    def den(sym, ref, vol_on, cap, amount, data=None, qqq=None):
        prev = b.LEV_MAX
        b.LEV_MAX = cap
        try:
            if data is None:
                data = {"days": common, "lev": px[sym], "div": px["SCHD"], "vol": vol if vol_on else {}, "trend": tr,
                        "start": "2012-01-01"}
                qqq = px["QQQ"]
            return b.simulate(data, qqq, ref=ref, monthly_krw=amount)
        finally:
            b.LEV_MAX = prev

    # ── B. 역사 보조 ──
    hist: dict[str, Any] = {"windows": {}, "yearly": {}}
    for name, sym, ref, v_on, cap, _ in c1.CASES:
        sim = den(sym, ref, v_on, cap, 100_000)
        nd, nv = sim["nav_days"], sim["navs"]
        hist["windows"][name] = {k: window_dd(nd, nv, a, z) for k, (a, z) in WINDOWS.items()}
        hist["yearly"][name] = yearly(nd, nv)
        if name == "Deneb":
            me: dict[str, tuple[float, float]] = {}
            for d, v, dv in zip(nd, sim["vals"], sim["div_vals"], strict=True):
                me[d[:7]] = (v, dv)
            hist["deneb_schd"] = {"months": list(me), "value": [x[0] for x in me.values()],
                                  "schd": [x[1] for x in me.values()]}
    pol = c1.capture_pol(px, common, i0, len(common) - 1, "YEAR", 100_000)
    hist["windows"]["Polaris"] = {k: window_dd(pol["dates"], pol["nav"], a, z) for k, (a, z) in WINDOWS.items()}
    hist["yearly"]["Polaris"] = yearly(pol["dates"], pol["nav"])
    hist["polaris_weights"] = {p: polaris_weights(px, common, i0, len(common) - 1, p) for p in ("YEAR", "BUY")}
    hist["savings_horizon"] = {
        f"r{int(r * 100)}": {str(y): {"pretax": save(100_000, y, r), "after_tax": save(100_000, y, r, 0.154)}
                             for y in (5, 10, 15, 20)} for r in (0.03, 0.04)}
    z = 0.0
    for _ in range(5):
        z = z * 1.04 + 12 * 100_000 + 6.5 * 100_000 * 0.04
    assert math.isclose(hist["savings_horizon"]["r4"]["5"]["pretax"], z, rel_tol=1e-12)

    # ── A. 장기 재표본 (full_report 와 같은 순서 · 같은 씨앗) ──
    syms = ["QQQ", "TQQQ", "SCHD", "SPY", "IEF", "GLD"]
    prices = np.array([[px[s][d] for s in syms] for d in days])
    rets = prices[1:] / prices[:-1] - 1
    sd = []
    cur = date(2026, 1, 1)
    while cur < date(2047, 1, 1):
        if cur.weekday() < 5:
            sd.append(cur.isoformat())
        cur += timedelta(days=1)
    targets = [max(i for i, d in enumerate(sd) if d[:4] == str(2026 + y)) for y in [5, 10, 15, 20]]
    monthidx = [i for i, d in enumerate(sd) if d >= "2027-01-01" and (i == len(sd) - 1 or d[:7] != sd[i + 1][:7])]
    rng = np.random.default_rng(20261018)
    names = ["Deneb", "Polaris", "QQQ 단독 적립"]
    dist = {k: {str(y): [] for y in [5, 10, 15, 20]} for k in names}
    tracks: dict[str, list] = {k: [] for k in names}
    t0 = time.time()
    for n in range(1000):
        starts = rng.integers(0, len(rets) - 252 + 1, size=math.ceil((len(sd) - 1) / 252))
        rr = np.concatenate([rets[a:a + 252] for a in starts], axis=0)[: len(sd) - 1]
        pa = np.vstack([np.ones(6) * 100, 100 * np.cumprod(1 + rr, axis=0)])
        pp = {s: dict(zip(sd, pa[:, j].tolist(), strict=True)) for j, s in enumerate(syms)}
        vv, tt = c1.signals(sd, pp["QQQ"])
        base = {"days": sd, "div": pp["SCHD"], "trend": tt, "start": "2027-01-01"}
        dn = den("", "T", True, 0.6, M, {**base, "lev": pp["TQQQ"], "vol": vv}, pp["QQQ"])
        qq = den("", None, False, 1.0, M, {**base, "lev": pp["QQQ"], "vol": {}}, pp["QQQ"])
        po = c1.capture_pol(pp, sd, next(i for i, d in enumerate(sd) if d >= "2027-01-01"), len(sd) - 1, "YEAR", M)
        assert dn["contrib"] == qq["contrib"] == po["paid"] == 240 * M
        for name, dd, vals in [("Deneb", dn["nav_days"], dn["vals"]), ("Polaris", po["dates"], po["values"]),
                               ("QQQ 단독 적립", qq["nav_days"], qq["vals"])]:
            di = {d: i for i, d in enumerate(dd)}
            for y, j in zip([5, 10, 15, 20], targets, strict=True):
                dist[name][str(y)].append(vals[di[sd[j]]])
            tracks[name].append([vals[di[sd[j]]] for j in monthidx])
        if (n + 1) % 50 == 0:
            print(json.dumps({"paths_done": n + 1, "elapsed_s": round(time.time() - t0)}), flush=True)

    led = json.loads(LEDGER.read_text(encoding="utf-8"))["resampling"]["summary"]
    summary: dict[str, Any] = {}
    fan: dict[str, Any] = {}
    for name in names:
        summary[name] = {}
        ta = np.array(tracks[name])
        fan[name] = {k: np.quantile(ta, q, axis=0).tolist() for k, q in [("p10", 0.1), ("p50", 0.5), ("p90", 0.9)]}
        for y, xs in dist[name].items():
            qs = {k: float(np.quantile(xs, q)) for k, q in [("min", 0), ("p10", 0.1), ("p50", 0.5), ("p90", 0.9)]}
            summary[name][y] = qs
            if name in led:
                for k in qs:
                    assert math.isclose(qs[k], led[name][y][k], rel_tol=1e-9), (name, y, k, qs[k], led[name][y][k])
    cmp = {}
    for y in ["5", "10", "15", "20"]:
        d_, p_, q_ = (np.array(dist[k][y]) for k in names)
        paid = M * 12 * int(y)
        cmp[y] = {"deneb_below_qqq": int((d_ < q_).sum()), "polaris_below_qqq": int((p_ < q_).sum()),
                  "below_paid": {k: int((np.array(dist[k][y]) < paid).sum()) for k in names}}
    out = {
        "spec_sha": sha(W / "calc2_spec.md"),
        "historical": hist,
        "resampling": {"paths": 1000, "seed": 20261018, "monthly_krw": M, "summary": summary, "compare": cmp,
                       "fan_months": list(range(1, 241)), "fan": fan},
        "checks": {"deneb_polaris_quantiles_reproduced": True, "deposits_240": True, "savings_5y_formula": True,
                   "decision": "설명용 — 새 PASS · OOS · 운영 변경 없음"},
    }
    (W / "calc2_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"summary": summary, "compare": cmp}, ensure_ascii=False))


if __name__ == "__main__":
    main()
