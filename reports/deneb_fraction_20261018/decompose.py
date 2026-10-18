"""원인 분해 — spec.md 덧붙임 그대로: 소수점 · 정수 이상형 · 정수 실거래형 (같은 200 경로).

    <numpy 있는 파이썬> reports/deneb_fraction_20261018/decompose.py
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
MODES = ("frac", "int_ideal", "int_live")


def run(days, L, D, vol, trend, usd, mode, start, KL=None, KD=None):
    integer = mode != "frac"
    q = {"TQQQ": 0.0, "SCHD": 0.0}
    cash = res = 0.0
    stage, off_seen, ep, l_max, started, tgt = 0, False, False, 0.0, False, 0.0
    rec = []
    i0 = next(i for i, d in enumerate(days) if d >= start and i > 0)
    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        kl, kd = (KL[i], KD[i]) if KL else (1, 1)
        if kl != 1:  # 분할: 주수 × k, 고점 ÷ k
            q["TQQQ"] *= kl
            l_max /= kl
        if kd != 1:
            q["SCHD"] *= kd
        new_month = d[:7] != prev[:7]
        if started and q["TQQQ"] > (1 - 1e-12 if integer else 0) and stage < 2:
            dd = 1 - L[i - 1] / kl / l_max if l_max > 0 else 0.0
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = (math.floor(q["TQQQ"]) if integer else q["TQQQ"]), 2
            elif dd >= STAGE1 and stage == 0:
                sell, stage = (max(1, math.floor(q["TQQQ"] / 2)) if integer else q["TQQQ"] / 2), 1
            if sell > 0:
                sell = min(sell, q["TQQQ"])
                res += sell * L[i] * (1 - FEE)
                q["TQQQ"] -= sell
                if not ep:
                    ep, off_seen = True, False
        if stage > 0 and ep:
            t_on = trend.get(prev)
            if t_on is False:
                off_seen = True
            if t_on and off_seen:
                if integer:
                    n = math.floor(res / (L[i] * (1 + FEE)))
                    q["TQQQ"] += n
                    cash += res - n * L[i] * (1 + FEE)
                else:
                    q["TQQQ"] += res * (1 - FEE) / L[i]
                res, ep, stage, l_max = 0.0, False, 0, L[i]
        if new_month:
            started = True
            v = vol.get(prev)
            share = b.LEV_MAX * (min(1.0, b.VOL_REF / v) if v and v > 0 else 1.0) * b.STAGE_B[stage]
            tgt = share
            px = {"TQQQ": L[i], "SCHD": D[i]}
            if mode == "int_live":
                cash += usd
                plan = fc.fast_choose(q, cash, px, share, FEE)
                for s, n in sorted(plan.items(), key=lambda kv: kv[1]):  # 팔기 먼저 (돈은 다음 달부터 씀)
                    if n < 0:
                        cash += -n * px[s] * (1 - FEE)
                    else:
                        cash -= n * px[s] * (1 + FEE)
                    q[s] += n
            elif mode == "int_ideal":
                cash += usd
                val = cash + q["TQQQ"] * px["TQQQ"] + q["SCHD"] * px["SCHD"]
                nl = max(round(share * val / px["TQQQ"]), 0)
                nd = max(math.floor((val - nl * px["TQQQ"]) / (px["SCHD"] * (1 + FEE))), 0)
                want = {"TQQQ": nl, "SCHD": nd}
                for s in ("TQQQ", "SCHD"):  # 팔기 먼저 — 그 돈을 같은 날 씀
                    n = want[s] - q[s]
                    if n < 0:
                        cash += -n * px[s] * (1 - FEE)
                        q[s] += n
                for s in ("TQQQ", "SCHD"):
                    n = want[s] - q[s]
                    if n > 0:
                        n = min(n, math.floor(cash / (px[s] * (1 + FEE))))
                        cash -= n * px[s] * (1 + FEE)
                        q[s] += n
            else:
                act_l, act_d = q["TQQQ"] * L[i], q["SCHD"] * D[i]
                active = act_l + act_d + usd
                tgt_l = share * active
                cm = usd
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
        if started:
            l_max = max(l_max, L[i])
            val = q["TQQQ"] * L[i] + q["SCHD"] * D[i] + cash + res
            if i == len(days) - 1 or d[:7] != days[i + 1][:7]:
                rec.append((d[:7], val, cash / val if val else 0.0, q["TQQQ"] * L[i] / val if val else 0.0, tgt))
    return rec


def main() -> None:
    px = {s: c.load(fc.R / f"data/research/div_mix_180/{s}.csv") for s in ["QQQ", "SCHD", "SPY", "IEF", "GLD"]}
    px["TQQQ"] = c.load(fc.R / "data/research/lev_div_181/TQQQ.csv")
    common = sorted(d for d in set.intersection(*(set(v) for v in px.values())) if d <= "2026-08-31")
    # 검증: 이 파일의 frac · int_live 가 calc.py 의 run 과 같은 값
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
    rng = np.random.default_rng(20261018)
    ends = {y: f"{2026 + y}-12" for y in fc.HORIZONS}
    acc = {str(a): {m: {"final": {str(y): [] for y in fc.HORIZONS}, "idle": [], "dev": []} for m in MODES} for a in fc.AMOUNTS}
    checked = False
    for n in range(fc.NPATH):
        starts = rng.integers(0, len(rets) - 252 + 1, size=math.ceil((len(sd) - 1) / 252))
        rr = np.concatenate([rets[a:a + 252] for a in starts], axis=0)[: len(sd) - 1]
        pa = np.vstack([np.ones(6) * 100, 100 * np.cumprod(1 + rr, axis=0)])
        vv, tt = c1.signals(sd, dict(zip(sd, pa[:, 0].tolist(), strict=True)))
        (L, KL), (D, KD) = fc.nominal(pa[:, 1].tolist(), "TQQQ"), fc.nominal(pa[:, 2].tolist(), "SCHD")
        for a in fc.AMOUNTS:
            usd = a / fc.FX
            recs = {m: run(sd, L, D, vv, tt, usd, m, "2027-01-01", KL, KD) for m in MODES}
            if not checked:
                ref_f = fc.run(sd, L, D, vv, tt, usd, False, "2027-01-01", KL, KD)
                ref_i = fc.run(sd, L, D, vv, tt, usd, True, "2027-01-01", KL, KD)
                assert abs(ref_f[-1][1] / recs["frac"][-1][1] - 1) < 1e-12
                assert abs(ref_i[-1][1] / recs["int_live"][-1][1] - 1) < 1e-12
                checked = True
            for m, rec in recs.items():
                mm = {r[0]: r for r in rec}
                for y, e in ends.items():
                    acc[str(a)][m]["final"][str(y)].append(mm[e][1])
                acc[str(a)][m]["idle"].append(float(np.mean([r[2] for r in rec])))
                acc[str(a)][m]["dev"].append(float(np.mean([abs(r[3] - r[4]) for r in rec])))
        if (n + 1) % 20 == 0:
            print("paths", n + 1, flush=True)
    out = {"paths": fc.NPATH, "summary": {}}
    for a in fc.AMOUNTS:
        A = acc[str(a)]
        f = {y: np.array(A["frac"]["final"][y]) for y in A["frac"]["final"]}
        row = {}
        for m in ("int_ideal", "int_live"):
            row[m] = {y: {k: float(np.quantile(np.array(A[m]["final"][y]) / f[y] - 1, qq)) for k, qq in
                          [("p10", .1), ("p50", .5), ("p90", .9)]} for y in f}
        row["idle"] = {m: float(np.mean(A[m]["idle"])) for m in MODES}
        row["dev"] = {m: float(np.mean(A[m]["dev"])) for m in MODES}
        out["summary"][str(a)] = row
    (W / "decompose_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
