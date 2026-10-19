"""문서 286 — Deneb 범위 맞추기(±N%p 벗어날 때만 팔기) vs 매달 맞추기. 1 회 판정.

사용 예::

    PYTHONPATH="src;.;.venv/Lib/site-packages" build/tcn-venv/Scripts/python.exe \
        -m scripts.run_band_286 run

모의 계좌는 285 ``simulate_mix``(금 있음) · 184 ``simulate(ref="T")``(금 없음)와 같고,
매달 맞추기에서 '팔기' 를 범위 밖일 때만 한다. ``band=None`` 이면 둘과 같아야 한다(실행 때 확인).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_brake_183 import (
    COST,
    CRISES,
    LEV_MAX,
    STAGE1,
    STAGE2,
    STAGE_B,
    VOL_REF,
    data_real,
    data_synth,
)
from scripts.run_brake_184 import (
    Book,
    irr,
    qqq_real,
    qqq_synth,
    simulate,
    summarize,
    tax_of,
)

from scripts import run_gold_silver_285 as g285

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "band_286"
BAND = 0.10
REPORT_BANDS = (0.05, 0.15, 0.20)
MIX = 0.5
MONTHLY_KRW = 300_000.0
WINDOW_MONTHS = 120
S2_WIN = ("2000-10", "2016-08")
R_WIN = ("2012-01", "2016-08")
S_START = "1986-02-01"
WIN_SHARE, WIN_BAD = 0.60, 0.40
S_MDD_SLACK, S_IRR_BAD = 0.02, 0.005


# ------------------------------------------------------------------ 모의 계좌 (순수)


def rebalance_ok(act_l: float, tgt_l: float, active: float, band: float | None) -> bool:
    """이번 달 팔아서 맞출지. ``band`` None = 매달(M)."""
    return band is None or abs(act_l - tgt_l) > band * active


def simulate_band(
    data: Mapping[str, Any],
    qqq: Mapping[str, float],
    gold: Mapping[str, float] | None = None,
    *,
    band: float | None = None,
    mix: float = MIX,
) -> dict[str, Any]:
    """184 T 계좌(+ 285 금 몫). 매달 맞추기의 '팔기' 만 ``rebalance_ok`` 일 때."""
    days, lev, div = data["days"], data["lev"], data["div"]
    vol, trend = data["vol"], data["trend"]
    mk = float(data.get("monthly_krw", MONTHLY_KRW))
    px: dict[str, Mapping[str, float]] = {"L": lev, "D": div, "Q": qqq}
    if gold is not None:
        px["G"] = gold
    bk = Book()
    res_cash = units = contrib = 0.0
    stage, off_seen = 0, False
    navs: list[float] = []
    nav_days: list[str] = []
    l_max = 0.0
    sales: list[dict[str, Any]] = []
    eps: list[dict[str, Any]] = []
    ep: dict[str, Any] | None = None
    taxes: dict[str, float] = {}
    flows: list[tuple[str, float]] = []
    rebal_months = 0
    i0 = next(i for i, d in enumerate(days) if d >= data["start"] and i > 0)

    def value(d: str) -> float:
        return float(sum(bk.q[s] * px[s][d] for s in px if bk.q[s])) + res_cash

    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        y = d[:4]
        new_month = d[:7] != prev[:7]
        if new_month and d[5:7] == "06" and str(int(y) - 1) not in taxes and units > 0:
            t = tax_of(bk.gain.get(str(int(y) - 1), 0.0))
            taxes[str(int(y) - 1)] = t
            v = value(d)
            if t > 0 and v > 0:
                f = max(1 - t / v, 0.0)
                bk.scale(f)
                res_cash *= f
                units *= f
        if units > 0 and bk.q["L"] > 0 and stage < 2:
            dd = 1 - lev[prev] / l_max if l_max > 0 else 0.0
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = bk.q["L"], 2
            elif dd >= STAGE1 and stage == 0:
                sell, stage = bk.q["L"] / 2, 1
            if sell > 0:
                res_cash += bk.sell("L", sell, lev[d], y)
                sales.append({"day": d, "price": lev[d], "dd": round(dd, 4)})
                if ep is None:
                    ep = {"start": d, "usd": 0.0, "qty": 0.0}
                    off_seen = False
                ep["usd"] += sell * lev[d]
                ep["qty"] += sell
        if stage > 0 and ep is not None:
            t_on = trend.get(prev)
            if t_on is False:
                off_seen = True
            if t_on and off_seen:
                cash, res_cash = res_cash, 0.0
                bk.buy("L", cash, lev[d])
                ep.update(end=d, avg_sale=ep["usd"] / ep["qty"], rebuy=lev[d])
                eps.append(ep)
                ep, stage = None, 0
                l_max = lev[d]
        if new_month:
            v_before = value(d)
            nav_now = v_before / units if units > 0 else 1.0
            units += mk / nav_now
            contrib += mk
            flows.append((d, mk))
            act_l, act_d = bk.q["L"] * lev[d], bk.q["D"] * div[d]
            act_m = bk.q["G"] * gold[d] if gold is not None else 0.0
            active = act_l + act_d + act_m + mk
            v = vol.get(prev)
            share = LEV_MAX * (min(1.0, VOL_REF / v) if v and v > 0 else 1.0) * STAGE_B[stage]
            tgt_l = share * active
            tgt_m = (active - tgt_l) * mix if gold is not None else 0.0
            tgt_d = active - tgt_l - tgt_m
            cash = mk
            if rebalance_ok(act_l, tgt_l, active, band):
                rebal_months += 1
                if act_l > tgt_l:
                    cash += bk.sell("L", (act_l - tgt_l) / lev[d], lev[d], y)
                if act_d > tgt_d:
                    cash += bk.sell("D", (act_d - tgt_d) / div[d], div[d], y)
                if gold is not None and act_m > tgt_m:
                    cash += bk.sell("G", (act_m - tgt_m) / gold[d], gold[d], y)
            need_l = max(tgt_l - bk.q["L"] * lev[d], 0.0)
            need_d = max(tgt_d - bk.q["D"] * div[d], 0.0)
            need_m = max(tgt_m - bk.q["G"] * gold[d], 0.0) if gold is not None else 0.0
            tot = need_l + need_d + need_m
            if tot > 0:
                bk.buy("L", cash * need_l / tot, lev[d])
                bk.buy("D", cash * need_d / tot, div[d])
                if gold is not None:
                    bk.buy("G", cash * need_m / tot, gold[d])
            else:
                bk.buy("D", cash, div[d])
        v = value(d)
        if units > 0:
            navs.append(v / units)
            nav_days.append(d)
            l_max = max(l_max, lev[d])
    end = days[-1]
    last_y = end[:4]
    taxes[last_y] = tax_of(bk.gain.get(last_y, 0.0))
    v_end = value(end) - taxes[last_y]
    unreal = sum(bk.q[s] * px[s][end] * (1 - COST) - bk.cost[s] for s in px if bk.q[s])
    liq_tax = tax_of(bk.gain.get(last_y, 0.0) + unreal) - taxes[last_y]
    return {
        "navs": navs,
        "nav_days": nav_days,
        "contrib": contrib,
        "flows": flows,
        "end": end,
        "after_tax_hold": v_end,
        "after_tax_liq": v_end - max(liq_tax, 0.0),
        "taxes": taxes,
        "sales": sales,
        "episodes": eps,
        "all_sells": bk.sells,
        "rebal_months": rebal_months,
    }


# ------------------------------------------------------------------ 판정 (순수)


def window_starts(lo: str, hi: str) -> list[str]:
    out, y, m = [], int(lo[:4]), int(lo[5:])
    while f"{y:04d}-{m:02d}" <= hi:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def add_months(ym: str, n: int) -> str:
    k = int(ym[:4]) * 12 + int(ym[5:]) - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def window_summary(pairs: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """(M 배수, B 배수) 들 → 이긴 비율 · 분포."""
    m = sorted(a for a, _ in pairs)
    b = sorted(x for _, x in pairs)
    diffs = [x - a for a, x in pairs]

    def q(xs: Sequence[float], p: float) -> float:
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)

    return {
        "windows": len(pairs),
        "band_ge_share": round(sum(x >= a for a, x in pairs) / len(pairs), 4),
        "median": {"M": round(statistics.median(m), 3), "B": round(statistics.median(b), 3)},
        "p10": {"M": q(m, 0.10), "B": q(b, 0.10)},
        "min": {"M": round(m[0], 3), "B": round(b[0], 3)},
        "p90": {"M": q(m, 0.90), "B": q(b, 0.90)},
        "worst_diff": round(min(diffs), 3),
        "best_diff": round(max(diffs), 3),
    }


def judge(res: Mapping[str, Any]) -> dict[str, Any]:
    """문서 286 §3 (순수)."""
    w = res["windows"]["S2"]
    s_m, s_b = res["S"]["M"], res["S"]["B10"]
    s2 = res["S2"]
    checks = {
        "windows_share": w["band_ge_share"] >= WIN_SHARE,
        "windows_median": w["median"]["B"] >= w["median"]["M"],
        "s_irr": s_b["irr_liq"] >= s_m["irr_liq"],
        "s_mdd": s_b["mdd"] <= s_m["mdd"] + S_MDD_SLACK,
        "robust_b5_b15": all(s2[k]["irr_liq"] >= s2["M"]["irr_liq"] for k in ("B5", "B15")),
    }
    if all(checks.values()):
        status = "채택 후보"
    elif w["band_ge_share"] < WIN_BAD or s_b["irr_liq"] < s_m["irr_liq"] - S_IRR_BAD:
        status = "불리"
    else:
        status = "불명"
    return {"status": status, "checks": checks}


# ------------------------------------------------------------------ 실행


def _arm_key(band: float | None) -> str:
    return "M" if band is None else f"B{round(band * 100)}"


def _row(sim: Mapping[str, Any], crises: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    s = summarize(sim, crises)
    return {
        **{k: s[k] for k in ("cagr", "mdd", "after_tax_liq_x", "irr_liq", "tax_paid_x")},
        "after_tax_liq_krw": round(sim["after_tax_liq"]),
        "taxes_krw": round(sum(sim["taxes"].values())),
        "sells": sim["all_sells"],
        "rebal_months": sim["rebal_months"],
        "crises": s["crises"],
    }


def sets() -> dict[str, tuple[dict[str, Any], dict[str, float], dict[str, float] | None, str]]:
    dr = dict(data_real(), monthly_krw=MONTHLY_KRW)
    ds = dict(data_synth(), monthly_krw=MONTHLY_KRW)
    qr = qqq_real()
    qs = qqq_synth(ds["days"], ds["rf"])
    gld = g285.align(dr["days"], g285._daily("GLD.csv"))
    gcf = g285.align(ds["days"], g285._daily("GC_F.csv"), g285.FEE["G"])
    return {
        "R": (dict(dr, start=max(dr["start"], min(gld))), qr, gld, "R"),
        "S2": (dict(ds, start=g285.S2_START), qs, gcf, "S"),
        "S": (dict(ds, start=S_START), qs, None, "S"),
        "R_T": (dr, qr, None, "R"),
    }


def evaluate() -> dict[str, Any]:
    out: dict[str, Any] = {}
    data_sets = sets()
    for name, (data, q, gold, ck) in data_sets.items():
        m = simulate_band(data, q, gold)
        if gold is not None:
            ref = g285.simulate_mix(data, q, {"G": gold})
        else:
            ref = simulate(data, q, ref="T", park="cash")
        if abs(ref["after_tax_liq"] - m["after_tax_liq"]) > 1e-6 * m["contrib"]:
            raise SystemExit(f"{name}: 매달 맞추기가 285 · 184 와 다르다")
        out[name] = {"M": _row(m, CRISES[ck])}
        for band in (BAND, *REPORT_BANDS):
            out[name][_arm_key(band)] = _row(simulate_band(data, q, gold, band=band), CRISES[ck])
        out[name]["period"] = [m["nav_days"][0], m["nav_days"][-1]]
        print(json.dumps({"set": name}), flush=True)
    out["windows"] = {}
    for name, (lo, hi) in (("S2", S2_WIN), ("R", R_WIN)):
        data, q, gold, _ck = data_sets[name]
        pairs = []
        for st in window_starts(lo, hi):
            end = add_months(st, WINDOW_MONTHS)
            w = dict(data, days=[d for d in data["days"] if d < f"{end}-01"], start=f"{st}-01")
            a = simulate_band(w, q, gold)
            b = simulate_band(w, q, gold, band=BAND)
            pairs.append((a["after_tax_liq"] / a["contrib"], b["after_tax_liq"] / b["contrib"]))
        out["windows"][name] = window_summary(pairs)
        print(json.dumps({"windows": name, "n": len(pairs)}), flush=True)
    out["amounts"] = {}
    for name in ("R", "S2"):
        data, q, gold, _ck = data_sets[name]
        for krw in (100_000.0, 1_000_000.0):
            dd = dict(data, monthly_krw=krw)
            a, b = simulate_band(dd, q, gold), simulate_band(dd, q, gold, band=BAND)
            out["amounts"][f"{name}_{int(krw)}"] = {
                k: {
                    "irr_liq": round(irr(s["flows"], s["end"], s["after_tax_liq"]), 4),
                    "after_tax_liq_krw": round(s["after_tax_liq"]),
                }
                for k, s in (("M", a), ("B10", b))
            }
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    g285.check_data()
    res = evaluate()
    out = {
        "git": sha,
        "completed_utc": datetime.now(UTC).isoformat(),
        "verdict": judge(res),
        "results": res,
        "note": "문서 286 1 회. 판정 = S2 10 년 창 · T 1986 ~ · B5 · B15 견고함.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) > 1 and argv[1] == "run":
        r = run()
        print(json.dumps(r["verdict"], ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
