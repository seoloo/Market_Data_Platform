"""문서 285 — 금은비로 금 ↔ 은 바꿔 들기(A) · Deneb 에 금 · 은 섞기(B). 1 회 판정.

사용 예::

    PYTHONPATH="src;.;.venv/Lib/site-packages" build/tcn-venv/Scripts/python.exe \
        -m scripts.run_gold_silver_285 run

A: World Bank 월평균 금 · 은(1975-01 ~ 2026-08), 한 달 늦춤. B: 184 모의 계좌에 금(· 은) 몫을 더함
— 금 몫 0 이면 184 ``simulate(ref="T")`` 와 같아야 한다(실행 때 확인).
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
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
    qqq_real,
    qqq_synth,
    simulate,
    summarize,
    tax_of,
    yearly,
)
from scripts.run_div_mix_180 import DATA_SHA as DIV_SHA
from scripts.run_div_mix_180 import own_hash
from scripts.run_lev_div_181 import LEV_SHA, lev_hash

from auto_trading.research.multi_asset import read_xlsx_sheet
from scripts import run_multi_asset_176 as m176

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "gold_silver_285"
METALS = REPO / "data" / "research" / "metals_285"
WB = REPO / "data" / "research" / "multi_asset" / "wb.xlsx"
WB_SHA = "ea1c350827878ea3bbe30e3cda16a13fd3bd5b409b8458940dc94a36b5a33154"
METAL_SHA = {
    "GC_F.csv": "26fa7702fa82d2f443e812b1c773a4551f64ff62d4b86695fdcdb5d555011f55",
    "GLD.csv": "fdce65f2c86b3efb6490ae5ac8202ce3841a2e4a6632909730c46fcd3e477401",
    "SI_F.csv": "a2920fbbf3ba8e68d4009683ef834bdb5c73eb52c38c3fcb82c83a540fa6848a",
    "SLV.csv": "faff8e49307dd0061332140aae69c09ba8c2f699e5fb60de13d4f162eed585a3",
}
A_FROM, A_TO = "1975-01", "2026-08"
HALF_END = "2000-12"
HI, LO, LO_CLASSIC = 80.0, 65.0, 50.0
SWITCH_COST = 0.002
FEE = {"G": 0.0040, "S": 0.0050}
US_W, METAL_W = 0.8, 0.2
MIX = 0.5
S2_START = "2000-09-01"
B_MDD_SLACK, B_TRADE_MDD, B_IRR_SLACK = 0.02, 0.05, 0.01


# ------------------------------------------------------------------ A: 순수 계산


def switch_states(gsr: Sequence[float], hi: float = HI, lo: float = LO) -> list[str]:
    """달마다 그 달 금은비를 본 뒤의 보유('G' · 'S'). 금으로 시작."""
    out, cur = [], "G"
    for g in gsr:
        if cur == "G" and g >= hi:
            cur = "S"
        elif cur == "S" and g <= lo:
            cur = "G"
        out.append(cur)
    return out


def metal_path(
    gold: Sequence[float],
    silver: Sequence[float],
    held: Sequence[str],
    *,
    cost: float = SWITCH_COST,
    fee: Mapping[str, float] = FEE,
) -> list[float]:
    """``held[j]`` = j−1 → j 사이 들고 있는 것('G' · 'S', j ≥ 1). 평가액(첫 값 1)."""
    eq = [1.0]
    for j in range(1, len(gold)):
        a = held[j]
        px = gold if a == "G" else silver
        v = eq[-1] * px[j] / px[j - 1] * (1 - fee[a] / 12)
        if j >= 2 and held[j - 1] != a:
            v *= (1 - cost) ** 2
        eq.append(v)
    return eq


def lagged(states: Sequence[str], lag: int = 2) -> list[str]:
    """달 i 에 정한 보유를 i + lag 수익부터(월평균이라 한 달 늦춤 → lag 2). 앞은 금."""
    return ["G"] * lag + list(states[: len(states) - lag])


def episodes(
    months: Sequence[str],
    held: Sequence[str],
    gold: Sequence[float],
    silver: Sequence[float],
    gsr: Sequence[float],
) -> list[dict[str, Any]]:
    """은을 든 구간: 시작 · 끝 달, 그동안 은 · 금 수익, 끝났는지."""
    out: list[dict[str, Any]] = []
    j = 1
    while j < len(held):
        if held[j] != "S":
            j += 1
            continue
        k = j
        while k + 1 < len(held) and held[k + 1] == "S":
            k += 1
        s_ret = silver[k] / silver[j - 1] - 1
        g_ret = gold[k] / gold[j - 1] - 1
        out.append(
            {
                "from": months[j - 1],
                "to": months[k],
                "months": k - j + 1,
                "gsr_from": round(gsr[j - 1], 1),
                "gsr_to": round(gsr[k], 1),
                "silver": round(s_ret, 4),
                "gold": round(g_ret, 4),
                "silver_won": s_ret > g_ret,
                "closed": k + 1 < len(held),
            }
        )
        j = k + 1
    return out


def path_stats(months: Sequence[str], eq: Sequence[float], lo: str, hi: str) -> dict[str, float]:
    idx = [i for i, m in enumerate(months) if lo <= m <= hi]
    a, b = idx[0], idx[-1]
    if a > 0:
        a -= 1  # 구간 첫 달 수익까지 포함
    seg = eq[a : b + 1]
    years = (len(seg) - 1) / 12
    peak, mdd = seg[0], 0.0
    for v in seg:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"cagr": round((seg[-1] / seg[0]) ** (1 / years) - 1, 4), "mdd": round(mdd, 4)}


def judge_a(res: Mapping[str, Any]) -> dict[str, Any]:
    sw, g = res["SW"], res["G"]
    closed = [e for e in res["episodes"]["SW"] if e["closed"]]
    checks = {
        "full": sw["full"]["cagr"] > g["full"]["cagr"],
        "halves": all(sw[h]["cagr"] > g[h]["cagr"] for h in ("first", "second")),
        "episodes_half": bool(closed) and sum(e["silver_won"] for e in closed) * 2 >= len(closed),
    }
    if all(checks.values()):
        status = "후속 가치(탐색)"
    elif not checks["full"]:
        status = "불리"
    else:
        status = "불명"
    return {"status": status, "checks": checks}


# ------------------------------------------------------------------ B: 모의 계좌


def simulate_mix(
    data: Mapping[str, Any],
    qqq: Mapping[str, float],
    metals: Mapping[str, Mapping[str, float]] | None = None,
    gsr: Mapping[str, float] | None = None,
    *,
    mix: float = MIX,
    hi: float = HI,
    lo: float = LO,
) -> dict[str, Any]:
    """184 ``simulate(ref="T", park="cash")`` + 나머지 몫 중 ``mix`` 를 금(``gsr`` 있으면 금 ↔ 은).

    ``metals`` 없으면 184 와 같은 계산이어야 한다."""
    days, lev, div = data["days"], data["lev"], data["div"]
    vol, trend = data["vol"], data["trend"]
    px: dict[str, Mapping[str, float]] = {"L": lev, "D": div, "Q": qqq}
    if metals:
        px.update(metals)
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
    metal, silver_months, switches = "G", 0, 0
    i0 = next(i for i, d in enumerate(days) if d >= data["start"] and i > 0)

    def value(d: str) -> float:
        return float(sum(bk.q[s] * px[s][d] for s in px if bk.q[s])) + res_cash

    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        y = d[:4]
        new_month = d[:7] != prev[:7]
        # 0) 6 월 첫 거래일: 지난해 세금
        if new_month and d[5:7] == "06" and str(int(y) - 1) not in taxes and units > 0:
            t = tax_of(bk.gain.get(str(int(y) - 1), 0.0))
            taxes[str(int(y) - 1)] = t
            v = value(d)
            if t > 0 and v > 0:
                f = max(1 - t / v, 0.0)
                bk.scale(f)
                res_cash *= f
                units *= f
        # 1) T 브레이크 (TQQQ 만)
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
        # 2) 재매수
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
        # 3) 매달 납입 + 다시 맞춤 (판 돈 현금 제외)
        if new_month:
            moved = 0.0
            if metals and gsr is not None:
                g = gsr.get(prev)
                want = metal
                if metal == "G" and g is not None and g >= hi:
                    want = "S"
                elif metal == "S" and g is not None and g <= lo:
                    want = "G"
                if want != metal:
                    moved = bk.sell(metal, bk.q[metal], px[metal][d], y)
                    metal = want
                    switches += 1
            v_before = value(d) + moved
            nav_now = v_before / units if units > 0 else 1.0
            units += data["monthly_krw"] / nav_now
            contrib += data["monthly_krw"]
            flows.append((d, data["monthly_krw"]))
            act_l, act_d = bk.q["L"] * lev[d], bk.q["D"] * div[d]
            mp = px[metal] if metals else None
            act_m = bk.q[metal] * mp[d] if mp is not None else 0.0
            active = act_l + act_d + act_m + data["monthly_krw"] + moved
            v = vol.get(prev)
            share = LEV_MAX * (min(1.0, VOL_REF / v) if v and v > 0 else 1.0) * STAGE_B[stage]
            tgt_l = share * active
            tgt_m = (active - tgt_l) * mix if mp is not None else 0.0
            tgt_d = active - tgt_l - tgt_m
            cash = data["monthly_krw"] + moved
            if act_l > tgt_l:
                cash += bk.sell("L", (act_l - tgt_l) / lev[d], lev[d], y)
            if act_d > tgt_d:
                cash += bk.sell("D", (act_d - tgt_d) / div[d], div[d], y)
            if mp is not None and act_m > tgt_m:
                cash += bk.sell(metal, (act_m - tgt_m) / mp[d], mp[d], y)
            need_l = max(tgt_l - bk.q["L"] * lev[d], 0.0)
            need_d = max(tgt_d - bk.q["D"] * div[d], 0.0)
            need_m = max(tgt_m - bk.q[metal] * mp[d], 0.0) if mp is not None else 0.0
            tot = need_l + need_d + need_m
            if tot > 0:
                bk.buy("L", cash * need_l / tot, lev[d])
                bk.buy("D", cash * need_d / tot, div[d])
                if mp is not None:
                    bk.buy(metal, cash * need_m / tot, mp[d])
            else:
                bk.buy("D", cash, div[d])
            if metals and metal == "S":
                silver_months += 1
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
        "silver_months": silver_months,
        "switches": switches,
    }


def judge_b(res: Mapping[str, Any], arm: str) -> dict[str, Any]:
    """문서 285 §3 (순수). ``res[set][arm]`` 은 184 ``summarize`` 결과."""

    def d(s: str, k: str) -> float:
        return float(res[s][arm][k]) - float(res[s]["T"][k])

    sets = ("R", "S2")
    checks = {
        s: {"irr_diff": round(d(s, "irr_liq"), 4), "mdd_diff": round(d(s, "mdd"), 4)} for s in sets
    }
    if all(d(s, "irr_liq") >= 0 and d(s, "mdd") <= B_MDD_SLACK for s in sets):
        status = "우위"
    elif all(d(s, "mdd") <= -B_TRADE_MDD and d(s, "irr_liq") >= -B_IRR_SLACK for s in sets):
        status = "낙폭 교환"
    elif any(d(s, "irr_liq") < -B_IRR_SLACK for s in sets):
        status = "불리"
    else:
        status = "불명"
    return {"status": status, "checks": checks}


# ------------------------------------------------------------------ 자료


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_data() -> None:
    bad = [n for n, h in METAL_SHA.items() if _sha(METALS / n) != h]
    if _sha(WB) != WB_SHA:
        bad.append("wb.xlsx")
    if m176.data_hash() != m176.EXPECTED_DATA_SHA:
        bad.append("176")
    if own_hash() != DIV_SHA or lev_hash() != LEV_SHA:
        bad.append("180 · 181")
    if bad:
        raise SystemExit(f"자료 해시가 문서 285 §1 과 다르다: {bad}")


def wb_metals() -> tuple[list[str], list[float], list[float]]:
    rows = read_xlsx_sheet(WB.read_bytes(), "Monthly Prices")
    head = next(r for r in rows if "Gold" in r)
    gi, si = head.index("Gold"), head.index("Silver")
    gold: dict[str, float] = {}
    silver: dict[str, float] = {}
    for r in rows:
        if r and len(r) > si and len(r[0]) == 7 and r[0][4] == "M":
            m = f"{r[0][:4]}-{r[0][5:]}"
            with contextlib.suppress(ValueError):
                gold[m], silver[m] = float(r[gi]), float(r[si])
    months = sorted(m for m in gold if A_FROM <= m <= A_TO and m in silver)
    return months, [gold[m] for m in months], [silver[m] for m in months]


def _daily(name: str) -> dict[str, float]:
    with (METALS / name).open(encoding="utf-8") as f:
        return {r["date"]: float(r["adjclose"]) for r in csv.DictReader(f)}


def align(days: Sequence[str], raw: Mapping[str, float], fee: float = 0.0) -> dict[str, float]:
    """``days`` 에 앞 값 채우기, ``fee`` 는 연 보수(날마다 1/252 씩 뺌). 자료 전 날은 뺀다."""
    out: dict[str, float] = {}
    last: float | None = None
    decay = 1.0
    for d in days:
        if d in raw:
            last = raw[d]
        if last is None:
            continue
        if out:
            decay *= 1 - fee / 252
        out[d] = last * decay
    return out


# ------------------------------------------------------------------ 실행


def evaluate_a() -> dict[str, Any]:
    months, gold, silver = wb_metals()
    gsr = [g / s for g, s in zip(gold, silver, strict=True)]
    arms = {
        "G": ["G"] * len(months),
        "S": ["S"] * len(months),
        "SW": lagged(switch_states(gsr)),
        "SW2": lagged(switch_states(gsr, HI, LO_CLASSIC)),
    }
    out: dict[str, Any] = {"months": [months[0], months[-1], len(months)], "episodes": {}}
    paths = {}
    for k, held in arms.items():
        eq = metal_path(gold, silver, held)
        paths[k] = eq
        out[k] = {
            "full": path_stats(months, eq, months[1], months[-1]),
            "first": path_stats(months, eq, months[1], HALF_END),
            "second": path_stats(months, eq, "2001-01", months[-1]),
            "silver_months": sum(1 for h in held[1:] if h == "S"),
        }
        if k.startswith("SW"):
            out["episodes"][k] = episodes(months, held, gold, silver, gsr)
    srt = sorted(gsr)
    out["gsr"] = {
        "min": round(srt[0], 1),
        "median": round(srt[len(srt) // 2], 1),
        "max": round(srt[-1], 1),
        "months_ge_hi": sum(g >= HI for g in gsr),
        "last": round(gsr[-1], 1),
    }
    us, _rf = m176._french()
    port: dict[str, Any] = {}
    for k in ("G", "SW"):
        eq, ms = [1.0], [months[1]]
        for j in range(2, len(months)):
            r_us = us.get(months[j])
            if r_us is None:
                break
            r_m = paths[k][j] / paths[k][j - 1] - 1
            eq.append(eq[-1] * (1 + US_W * r_us + METAL_W * r_m))
            ms.append(months[j])
        port[k] = path_stats(ms, eq, ms[1], ms[-1]) | {"to": ms[-1]}
    us_eq, us_ms = [1.0], [months[1]]
    for m in months[2:]:
        if m not in us:
            break
        us_eq.append(us_eq[-1] * (1 + us[m]))
        us_ms.append(m)
    port["US"] = path_stats(us_ms, us_eq, us_ms[1], us_ms[-1]) | {"to": us_ms[-1]}
    out["portfolio_us80_metal20"] = port
    # 확인: 2000-09 ~ 선물 월말 값, 같은 규칙 (월말이라 늦춤 1)
    gd, sd = _daily("GC_F.csv"), _daily("SI_F.csv")
    me: dict[str, tuple[float, float]] = {}
    for d in sorted(set(gd) & set(sd)):
        me[d[:7]] = (gd[d], sd[d])
    fm = [m for m in sorted(me) if "2000-09" <= m <= A_TO]
    fg, fs = [me[m][0] for m in fm], [me[m][1] for m in fm]
    fr = [a / b for a, b in zip(fg, fs, strict=True)]
    out["futures_check"] = {
        k: path_stats(fm, metal_path(fg, fs, h), fm[1], fm[-1])
        for k, h in {"G": ["G"] * len(fm), "SW": lagged(switch_states(fr), 1)}.items()
    }
    out["verdict"] = judge_a(out)
    return out


def evaluate_b() -> dict[str, Any]:
    gcf, sif = _daily("GC_F.csv"), _daily("SI_F.csv")
    dr = dict(data_real(), monthly_krw=300_000.0)
    ds = dict(data_synth(), monthly_krw=300_000.0, start=S2_START)
    sets = {
        "R": (dr, qqq_real(), {"G": _daily("GLD.csv"), "S": _daily("SLV.csv")}, 0.0),
        "S2": (ds, None, {"G": gcf, "S": sif}, 1.0),
    }
    out: dict[str, Any] = {}
    for name, (data, qqq, raw, use_fee) in sets.items():
        days = data["days"]
        q = qqq if qqq is not None else qqq_synth(days, data["rf"])
        metals = {k: align(days, v, FEE[k] * use_fee) for k, v in raw.items()}
        start = max(data["start"], *(min(m) for m in metals.values()))
        data = dict(data, start=start)
        g_al, s_al = align(days, gcf), align(days, sif)
        gsr = {d: g_al[d] / s_al[d] for d in g_al if d in s_al}
        base = simulate(data, q, ref="T", park="cash")
        t = simulate_mix(data, q)
        same = all(
            abs(a - b) <= 1e-9 * max(1.0, abs(a))
            for a, b in zip(base["navs"], t["navs"], strict=True)
        )
        if not same or abs(base["after_tax_liq"] - t["after_tax_liq"]) > 1e-6 * t["contrib"]:
            raise SystemExit(f"{name}: 금 몫 0 계산이 184 T 와 다르다")
        crises = CRISES["R" if name == "R" else "S"]
        sims = {
            "T": t,
            "TG": simulate_mix(data, q, metals),
            "TGS": simulate_mix(data, q, metals, gsr),
        }
        out[name] = {k: summarize(s, crises) for k, s in sims.items()}
        out[name]["period"] = [t["nav_days"][0], t["nav_days"][-1]]
        out[name]["contrib_krw"] = t["contrib"]
        out[name]["yearly"] = {k: yearly(s) for k, s in sims.items()}
        out[name]["silver_months"] = sims["TGS"]["silver_months"]
        out[name]["switches"] = sims["TGS"]["switches"]
        out[name]["after_tax_liq_krw"] = {k: round(s["after_tax_liq"]) for k, s in sims.items()}
        print(json.dumps({"set": name}), flush=True)
    out["verdict"] = {arm: judge_b(out, arm) for arm in ("TG", "TGS")}
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    check_data()
    a = evaluate_a()
    print(json.dumps({"A": a["verdict"]["status"]}, ensure_ascii=False), flush=True)
    b = evaluate_b()
    out = {
        "git": sha,
        "completed_utc": datetime.now(UTC).isoformat(),
        "verdict": {"A": a.pop("verdict"), "B": b.pop("verdict")},
        "A": a,
        "B": b,
        "note": "문서 285 1 회. A 월평균 1975 ~ 2026-08, "
        "B 184 T + 금(· 은) 몫, R 2012 ~ · S2 2000-09 ~.",
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
        v = r["verdict"]
        print(json.dumps({"A": v["A"]["status"], **{k: x["status"] for k, x in v["B"].items()}},
                         ensure_ascii=False))  # fmt: skip
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
