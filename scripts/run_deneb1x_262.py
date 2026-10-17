"""문서 262 — 1배 Deneb 참고 계산 (사전 등록 0825e95).

사용 예::

    python -m scripts.run_deneb1x_262   # 한 번 → reports/deneb1x_262/result.json

Deneb 규칙(184 ``simulate``)의 레버리지 자리에 QQQ(1 배)를 넣고, QQQ 단독 · 고정 60/40 ·
변동성 맞춤 · Polaris 와 같은 날 같은 돈(매달 20 만 원)으로 비교한다. 세전 · 비용 0.15% · 환율 고정.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import scripts.run_brake_183 as r183
import scripts.run_brake_184 as rb
import scripts.run_namu_core_compare as nc

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "deneb1x_262"
END = "2026-08-31"
MONTHLY = 200_000.0
COST = 0.0015
WINDOWS = {"R": "2012-01-01", "L": "2005-01-01"}
CRISES = {
    "2008 금융위기": ("2007-11-01", "2009-03-31"),
    "2018 4분기": ("2018-10-01", "2018-12-31"),
    "2020 코로나": ("2020-02-01", "2020-04-30"),
    "2022 금리 인상": ("2022-01-01", "2022-12-31"),
}
DIV = REPO / "data" / "research" / "div_mix_180"


def _polaris_policy() -> Any:
    path = REPO / "reports" / "deneb_polaris_report_20261017" / "claude_draft" / "calc.py"
    spec = importlib.util.spec_from_file_location("claude_draft_calc", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.polaris_policy


def months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def metrics(days: list[str], vals: list[float], navs: list[float], paid: list[float]) -> dict[str, Any]:
    mdd, pi, ti, best = 0.0, 0, 0, 0
    for i, n in enumerate(navs):
        if n > navs[best]:
            best = i
        dd = n / navs[best] - 1
        if dd < mdd:
            mdd, pi, ti = dd, best, i
    rec = next((i for i in range(ti, len(navs)) if navs[i] >= navs[pi]), None)
    gap = [v - p for v, p in zip(vals, paid, strict=True)]
    gi = min(range(len(gap)), key=gap.__getitem__)
    crises: dict[str, float] = {}
    for name, (a, b) in CRISES.items():
        idx = [i for i, d in enumerate(days) if a <= d <= b]
        if not idx:
            continue
        pk, worst = navs[idx[0]], 0.0
        for i in idx:
            pk = max(pk, navs[i])
            worst = min(worst, navs[i] / pk - 1)
        crises[name] = worst
    years = months_between(days[0], days[-1]) / 12
    return {
        "start": days[0],
        "end": days[-1],
        "paid": paid[-1],
        "final": vals[-1],
        "profit": vals[-1] - paid[-1],
        "cagr_twr": (navs[-1] / navs[0]) ** (1 / years) - 1,
        "mdd": mdd,
        "mdd_peak": days[pi],
        "mdd_trough": days[ti],
        "recovered": days[rec] if rec is not None else None,
        "recovery_months": months_between(days[pi], days[rec]) if rec is not None else None,
        "worst_gap": gap[gi],
        "worst_gap_day": days[gi],
        "crises": crises,
    }


def deneb_data(lev: Mapping[str, float], div: Mapping[str, float], start: str) -> dict[str, Any]:
    qqq = nc.load(DIV / "QQQ.csv")
    qd = sorted(d for d in qqq if d <= END)
    vol, trend = r183.signals(qd, qqq)
    days = sorted(d for d in set(lev) & set(div) & set(qqq) if d <= END)
    return {"days": days, "lev": lev, "div": div, "vol": vol, "trend": trend, "start": start}


def run_sim(data: Mapping[str, Any], *, ref: str | None, vol: bool, lev_max: float = 0.6) -> dict[str, Any]:
    old = rb.LEV_MAX
    rb.LEV_MAX = lev_max
    try:
        d = {**data, "vol": data["vol"] if vol else {}}
        sim = rb.simulate(d, nc.load(DIV / "QQQ.csv"), ref=ref, park="cash", monthly_krw=MONTHLY)
    finally:
        rb.LEV_MAX = old
    days = sim["nav_days"]
    fl = dict(sim["flows"])
    paid, acc = [], 0.0
    for x in days:
        acc += fl.get(x, 0.0)
        paid.append(acc)
    m = metrics(days, sim["vals"], sim["navs"], paid)
    m.update(sells=sim["all_sells"], brake_sells=len(sim["sales"]))
    return m


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    rb.TAX = 0.0
    assert rb.COST == COST
    qqq = nc.load(DIV / "QQQ.csv")
    schd = nc.load(DIV / "SCHD.csv")
    dvy = nc.load(DIV / "DVY.csv")
    base = sorted(d for d in set(qqq) & set(dvy) if "2004-06-01" <= d <= END)
    div_long = nc.chain(schd, nc.rets(dvy, base), base)
    tqqq = nc.load(REPO / "data" / "research" / "lev_div_181" / "TQQQ.csv")
    polaris_policy = _polaris_policy()
    px = {s: nc.load(DIV / f"{s}.csv") for s in nc.TARGET}
    pdays = sorted(d for d in set.intersection(*(set(px[s]) for s in nc.TARGET)) if d <= END)
    out: dict[str, Any] = {"prereg": "0825e95"}
    for w, start in WINDOWS.items():
        div = schd if w == "R" else div_long
        data = deneb_data(qqq, div, start)
        res = {
            "Q": run_sim(data, ref=None, vol=False, lev_max=1.0),
            "F": run_sim(data, ref=None, vol=False),
            "FB": run_sim(data, ref="T", vol=False),
            "V": run_sim(data, ref=None, vol=True),
            "D1": run_sim(data, ref="T", vol=True),
        }
        i0 = next(k for k, d in enumerate(pdays) if d >= start)
        pol = polaris_policy(px, pdays, i0, len(pdays) - 1, "YEAR", lambda k: MONTHLY, COST, 0.0)
        res["P"] = metrics(pol["days"], pol["vals"], pol["navs"], pol["paid"])
        if w == "R":
            res["D3"] = run_sim(deneb_data(tqqq, schd, start), ref="T", vol=True)
        out[w] = res
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> int:
    out = run()
    for w in WINDOWS:
        print(f"== {w}")
        for k, m in out[w].items():
            cr = " ".join(f"{c[:4]} {v:+.0%}" for c, v in m["crises"].items())
            rec = f"{m['recovery_months']}개월" if m["recovered"] else "미회복"
            print(
                f"{k:3} {m['start']}~ 원금 {m['paid'] / 1e4:,.0f}만 끝 {m['final'] / 1e4:,.0f}만"
                f" 연 {m['cagr_twr']:+.1%} 낙폭 {m['mdd']:.1%} 회복 {rec}"
                f" 원금밑 {m['worst_gap'] / 1e4:,.0f}만 매도 {m.get('sells', '-')}"
                f"/{m.get('brake_sells', '-')} | {cr}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
