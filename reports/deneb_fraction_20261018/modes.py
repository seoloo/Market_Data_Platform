"""2차 — 적립 방식 6가지 (spec.md '2차' 그대로, 참고 계산).

    <numpy 있는 파이썬> reports/deneb_fraction_20261018/modes.py
"""

from __future__ import annotations

import importlib.util
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

W = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("fc", W / "calc.py")
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)
b, c, c1 = fc.b, fc.c, fc.c1
FEE, STAGE1, STAGE2 = fc.FEE, fc.STAGE1, fc.STAGE2
BUF = 1.03
MODES = ("A", "B", "C", "D", "E", "F")
SLIP = 0.0005
if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "v3":
    MODES = ("A", "D", "D2", "E", "D_s", "D2_s")
AMOUNTS = [100_000, 300_000, 1_000_000]
SYMS = ("TQQQ", "SCHD")


def buy_integer_daily(q, cash, px, share):
    """가장 모자란 종목부터 1 주씩 (+3% · 비용으로 살 수 있을 때까지). 사기만."""
    while True:
        val = cash + sum(q[s] * px[s] for s in SYMS)
        tgt = {"TQQQ": share * val, "SCHD": (1 - share) * val}
        order = sorted(SYMS, key=lambda s: -(tgt[s] - q[s] * px[s]))
        done = True
        for s in order:
            if cash >= px[s] * BUF * (1 + FEE):
                q[s] += 1
                cash -= px[s] * (1 + FEE)
                done = False
                break
        if done:
            return cash


def buy_frac_daily(q, cash, px, share, fee=FEE):
    val = cash + sum(q[s] * px[s] for s in SYMS)
    need = {s: max((share if s == "TQQQ" else 1 - share) * val - q[s] * px[s], 0.0) for s in SYMS}
    tot = sum(need.values())
    for s in SYMS:
        part = cash * (need[s] / tot if tot > 0 else (0.0 if s == "TQQQ" else 1.0))
        q[s] += part * (1 - fee) / px[s]
    return 0.0


def run(days, L, D, vol, trend, usd, mode, start, ndays, KL=None, KD=None):
    integer = mode in ("A", "B", "C")
    fee = FEE + SLIP if mode.endswith("_s") else FEE
    mode = mode[:-2] if mode.endswith("_s") else mode
    q = {"TQQQ": 0.0, "SCHD": 0.0}
    cash = pend = res = 0.0
    stage, off_seen, ep, l_max, started, base = 0, False, False, 0.0, False, 0.0
    units, navs, rec = 0.0, [], []
    i0 = next(i for i, d in enumerate(days) if d >= start and i > 0)
    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        kl, kd = (KL[i], KD[i]) if KL else (1, 1)
        if kl != 1:  # 분할: 주수 × k, 고점 ÷ k
            q["TQQQ"] *= kl
            l_max /= kl
        if kd != 1:
            q["SCHD"] *= kd
        px = {"TQQQ": L[i], "SCHD": D[i]}
        new_month = d[:7] != prev[:7]
        cash += pend  # 어제 판 돈
        pend = 0.0
        val0 = q["TQQQ"] * L[i] + q["SCHD"] * D[i] + cash + res
        dep = 0.0
        if mode in ("C", "D", "F"):  # D2 는 매달 한 번 (아래 elif)
            dep = usd / ndays[d[:7]]
        elif new_month:
            dep = usd
        if dep:
            nav = val0 / units if units > 0 else 1.0
            units += dep / nav
        # 브레이크 (F 는 없음)
        if mode != "F" and started and q["TQQQ"] > (1 - 1e-12 if integer else 0) and stage < 2:
            dd = 1 - L[i - 1] / kl / l_max if l_max > 0 else 0.0
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = (math.floor(q["TQQQ"]) if integer else q["TQQQ"]), 2
            elif dd >= STAGE1 and stage == 0:
                sell, stage = (max(1, math.floor(q["TQQQ"] / 2)) if integer else q["TQQQ"] / 2), 1
            if sell > 0:
                sell = min(sell, q["TQQQ"])
                res += sell * L[i] * (1 - fee)
                q["TQQQ"] -= sell
                if not ep:
                    ep, off_seen = True, False
        if mode != "F" and stage > 0 and ep:
            t_on = trend.get(prev)
            if t_on is False:
                off_seen = True
            if t_on and off_seen:
                if integer:
                    n = math.floor(res / (L[i] * (1 + FEE)))
                    q["TQQQ"] += n
                    cash += res - n * L[i] * (1 + FEE)
                else:
                    q["TQQQ"] += res * (1 - fee) / L[i]
                res, ep, stage, l_max = 0.0, False, 0, L[i]
        if new_month:
            v = vol.get(prev)
            base = b.LEV_MAX * (min(1.0, b.VOL_REF / v) if v and v > 0 else 1.0)
        share = base * b.STAGE_B[stage] if mode != "F" else 0.6
        started = started or new_month or dep > 0
        cash += dep
        if mode == "A" and new_month:
            plan = fc.fast_choose(q, cash, px, share, FEE)
            for s, n in sorted(plan.items(), key=lambda kv: kv[1]):
                cash += -n * px[s] * (1 - FEE) if n < 0 else -n * px[s] * (1 + FEE)
                q[s] += n
        elif mode == "B":
            if new_month:
                plan = fc.fast_choose(q, cash, px, share, FEE)
                for s, n in plan.items():
                    if n < 0:
                        pend += -n * px[s] * (1 - FEE)
                        q[s] += n
                for s, n in plan.items():
                    if n > 0:
                        cash -= n * px[s] * (1 + FEE)
                        q[s] += n
            else:
                cash = buy_integer_daily(q, cash, px, share)
        elif mode == "C":
            if new_month:
                val = cash + sum(q[s] * px[s] for s in SYMS)
                for s in SYMS:
                    over = q[s] * px[s] - (share if s == "TQQQ" else 1 - share) * val
                    if over > px[s]:
                        n = min(math.floor(over / px[s]), math.floor(q[s]))
                        pend += n * px[s] * (1 - FEE)
                        q[s] -= n
            cash = buy_integer_daily(q, cash, px, share)
        elif mode in ("D", "D2"):
            if new_month:
                val = cash + sum(q[s] * px[s] for s in SYMS)
                for s in SYMS:
                    over = q[s] * px[s] - (share if s == "TQQQ" else 1 - share) * val
                    if over > 0:
                        n = over / px[s]
                        pend += n * px[s] * (1 - fee)
                        q[s] -= n
            if cash > 0:
                cash = buy_frac_daily(q, cash, px, share, fee)
        elif mode == "E" and new_month:  # simulate 회계
            cash -= dep
            act_l, act_d = q["TQQQ"] * L[i], q["SCHD"] * D[i]
            active = act_l + act_d + dep
            tgt_l = share * active
            cm = dep
            if act_l > tgt_l:
                n = (act_l - tgt_l) / L[i]
                cm += n * L[i] * (1 - FEE)
                q["TQQQ"] -= n
            tgt_d = active - tgt_l
            if act_d > tgt_d:
                n = (act_d - tgt_d) / D[i]
                cm += n * D[i] * (1 - FEE)
                q["SCHD"] -= n
            need_l = max(tgt_l - q["TQQQ"] * L[i], 0.0)
            need_d = max(tgt_d - q["SCHD"] * D[i], 0.0)
            tot = need_l + need_d
            if tot > 0:
                q["TQQQ"] += cm * need_l / tot * (1 - FEE) / L[i]
                q["SCHD"] += cm * need_d / tot * (1 - FEE) / D[i]
            else:
                q["SCHD"] += cm * (1 - FEE) / D[i]
        elif mode == "F":
            q["TQQQ"] += cash * 0.6 * (1 - FEE) / L[i]
            q["SCHD"] += cash * 0.4 * (1 - FEE) / D[i]
            cash = 0.0
        if started:
            l_max = max(l_max, L[i])
            val = q["TQQQ"] * L[i] + q["SCHD"] * D[i] + cash + res + pend
            if units > 0:
                navs.append(val / units)
            if i == len(days) - 1 or d[:7] != days[i + 1][:7]:
                rec.append((d[:7], val, (cash + pend) / val if val else 0.0))
    a = np.asarray(navs)
    mdd = float((a / np.maximum.accumulate(a) - 1).min()) if len(a) else 0.0
    return rec, mdd


def main() -> None:
    px = {s: c.load(fc.R / f"data/research/div_mix_180/{s}.csv") for s in ["QQQ", "SCHD", "SPY", "IEF", "GLD"]}
    px["TQQQ"] = c.load(fc.R / "data/research/lev_div_181/TQQQ.csv")
    common = sorted(d for d in set.intersection(*(set(v) for v in px.values())) if d <= "2026-08-31")
    i0 = next(i for i, d in enumerate(common) if d >= "2012-01-01")
    days = common[i0:]
    syms = ["QQQ", "TQQQ", "SCHD", "SPY", "IEF", "GLD"]
    prices = np.array([[px[s][d] for s in syms] for d in days])
    rets = prices[1:] / prices[:-1] - 1
    sd, cur = [], date(2026, 1, 1)
    while cur < date(2047, 1, 1):
        if cur.weekday() < 5:
            sd.append(cur.isoformat())
        cur += timedelta(days=1)
    ndays: dict[str, int] = {}
    for d in sd:
        ndays[d[:7]] = ndays.get(d[:7], 0) + 1
    rng = np.random.default_rng(20261018)
    ends = {y: f"{2026 + y}-12" for y in fc.HORIZONS}
    acc = {str(a): {m: {"final": {str(y): [] for y in fc.HORIZONS}, "idle": [], "mdd": []} for m in MODES} for a in AMOUNTS}
    checked = False
    for n in range(fc.NPATH):
        starts = rng.integers(0, len(rets) - 252 + 1, size=math.ceil((len(sd) - 1) / 252))
        rr = np.concatenate([rets[a:a + 252] for a in starts], axis=0)[: len(sd) - 1]
        pa = np.vstack([np.ones(6) * 100, 100 * np.cumprod(1 + rr, axis=0)])
        vv, tt = c1.signals(sd, dict(zip(sd, pa[:, 0].tolist(), strict=True)))
        (L, KL), (D, KD) = fc.nominal(pa[:, 1].tolist(), "TQQQ"), fc.nominal(pa[:, 2].tolist(), "SCHD")
        for a in AMOUNTS:
            usd = a / fc.FX
            out = {m: run(sd, L, D, vv, tt, usd, m, "2027-01-01", ndays, KL, KD) for m in MODES}
            if not checked:
                ref_i = fc.run(sd, L, D, vv, tt, usd, True, "2027-01-01", KL, KD)
                ref_f = fc.run(sd, L, D, vv, tt, usd, False, "2027-01-01", KL, KD)
                assert abs(ref_i[-1][1] / out["A"][0][-1][1] - 1) < 1e-9, (ref_i[-1][1], out["A"][0][-1][1])
                assert abs(ref_f[-1][1] / out["E"][0][-1][1] - 1) < 1e-9, (ref_f[-1][1], out["E"][0][-1][1])
                checked = True
            for m, (rec, mdd) in out.items():
                mm = {r[0]: r for r in rec}
                for y, e in ends.items():
                    acc[str(a)][m]["final"][str(y)].append(mm[e][1])
                acc[str(a)][m]["idle"].append(float(np.mean([r[2] for r in rec])))
                acc[str(a)][m]["mdd"].append(mdd)
        if (n + 1) % 20 == 0:
            print("paths", n + 1, flush=True)
    res = {"paths": fc.NPATH, "summary": {}}
    for a in AMOUNTS:
        A_ = acc[str(a)]
        base = {y: np.array(A_["A"]["final"][y]) for y in A_["A"]["final"]}
        row = {}
        for m in MODES:
            row[m] = {
                "vs_A": {y: {k: float(np.quantile(np.array(A_[m]["final"][y]) / base[y] - 1, qq))
                             for k, qq in [("p10", .1), ("p50", .5), ("p90", .9)]} for y in base},
                "final_p50": {y: float(np.median(A_[m]["final"][y])) for y in base},
                "idle": float(np.mean(A_[m]["idle"])),
                "mdd_p50": float(np.median(A_[m]["mdd"])),
            }
        res["summary"][str(a)] = row
    (W / ("modes3_result.json" if "D2" in MODES else "modes_result.json")).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for a in AMOUNTS:
        for m in MODES:
            r = res["summary"][str(a)][m]
            print(a, m, {y: round(v["p50"] * 100, 1) for y, v in r["vs_A"].items()}, "idle", round(r["idle"] * 100, 2),
                  "mdd", round(r["mdd_p50"] * 100, 1), "final20", round(r["final_p50"]["20"]))


if __name__ == "__main__":
    main()
