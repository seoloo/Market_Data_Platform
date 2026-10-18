"""Deneb 정수 주 vs 소수점 — spec.md 그대로 (참고 계산).

    <numpy 있는 파이썬> reports/deneb_fraction_20261018/calc.py
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

W = Path(__file__).resolve().parent
R = W.parents[1]
sys.path[:0] = [str(R), str(R / "src"), str(R / "reports/deneb_polaris_report_20261017/blue_manual")]
sys.path.append(str(R / ".venv/Lib/site-packages"))
from scripts import run_brake_184 as b  # noqa: E402
from scripts import run_namu_core_compare as c  # noqa: E402

import calc as c1  # noqa: E402  (blue_manual: signals)
from auto_trading.ops.deneb_guard import STAGE1, STAGE2  # noqa: E402
from auto_trading.ops.deneb_live import choose, limit_price  # noqa: E402

FEE = b.COST
FX = 1344.5
START = {"TQQQ": 81.28, "SCHD": 33.04}
SPLIT = {"TQQQ": (160.0, 2), "SCHD": (80.0, 3)}
AMOUNTS = [100_000, 200_000, 300_000, 500_000, 1_000_000]
HORIZONS = [5, 10, 20]
NPATH = 200


def fast_choose(qty, cash, last, share, fee):
    """``deneb_live.choose`` 와 같은 답을 빠르게 — 오차는 종목마다 볼록하고 돈 제약은 매수에만 걸리므로,
    종목마다 (제약 없는 최적 ±3) ∪ (0 ~ 살 수 있는 최대) 만 보면 같은 최적이 나온다. 아래 check 로 대조."""
    syms = ("TQQQ", "SCHD")
    total = cash + sum(qty.get(s, 0.0) * last[s] for s in syms)
    target = {"TQQQ": share * total, "SCHD": (1 - share) * total}
    cands = []
    for s in syms:
        hold = qty.get(s, 0.0)
        over = hold * last[s] - target[s]
        lo = -math.floor(over / last[s]) if over > last[s] else 0
        lo = max(lo, -math.floor(hold))
        hi = math.floor(cash / (limit_price(last[s], "BUY") * (1 + fee))) if cash > 0 else 0
        star = round((target[s] - hold * last[s]) / last[s])
        c_ = set(range(max(lo, star - 3), min(hi, star + 3) + 1)) | set(range(max(lo, 0), hi + 1)) | {lo, hi}
        cands.append(sorted(n for n in c_ if lo <= n <= hi))
    best = None
    lpd = limit_price(last["SCHD"], "BUY") * (1 + fee)
    lod, hid = cands[1][0], cands[1][-1]
    stard = (target["SCHD"] - qty.get("SCHD", 0.0) * last["SCHD"]) / last["SCHD"]
    for nl in cands[0]:
        spend_l = nl * limit_price(last["TQQQ"], "BUY") * (1 + fee) if nl > 0 else 0.0
        if spend_l > cash + 1e-9:
            continue
        cap = min(hid, math.floor((cash - spend_l + 1e-9) / lpd)) if hid > 0 else hid
        cap = max(cap, min(0, hid))
        base = min(max(round(stard), lod), cap)
        for nd in {base - 1, base, base + 1, math.floor(stard), math.ceil(stard)}:
            if not lod <= nd <= cap:
                continue
            d = {"TQQQ": nl, "SCHD": nd}
            spend = sum(n * limit_price(last[s], "BUY") * (1 + fee) for s, n in d.items() if n > 0)
            if spend > cash + 1e-9:
                continue
            err = sum((target[s] - (qty.get(s, 0.0) + d[s]) * last[s]) ** 2 for s in syms)
            key = (round(err, 6), abs(nl) + abs(nd))
            if best is None or key < best[0]:
                best = (key, d)
    return {s: n for s, n in (best[1] if best else {}).items() if n != 0}


def check_fast(trials: int = 3000) -> int:
    rnd = np.random.default_rng(7)
    bad = 0
    for _ in range(trials):
        last = {"TQQQ": float(rnd.uniform(40, 160)), "SCHD": float(rnd.uniform(20, 80))}
        qty = {"TQQQ": float(rnd.integers(0, 40)), "SCHD": float(rnd.integers(0, 80))}
        cash = float(rnd.uniform(0, 1500))
        share = float(rnd.choice([0.0, 0.15, 0.3, 0.45, 0.6]))
        if choose(qty, cash, last, share, FEE) != fast_choose(qty, cash, last, share, FEE):
            bad += 1
    return bad


def run(days, L, D, vol, trend, monthly_usd, integer: bool, start: str, KL=None, KD=None):
    """Deneb (ref T · 현금 보관 · 세전). L · D = 그날 가격(분할 반영된 명목가). 달말 기록을 돌려줌."""
    q = {"TQQQ": 0.0, "SCHD": 0.0}
    cash = res = 0.0
    stage, off_seen, ep = 0, False, False
    l_max, started = 0.0, False
    rec = []  # (month, value, idle_cash, w_lev, target_share)
    tgt_share = 0.0
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
        # 1) 브레이크 (T: TQQQ 자체 고점)
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
        # 2) 되사기
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
                res, ep, stage = 0.0, False, 0
                l_max = L[i]
        # 3) 매달 납입 + 맞춤
        if new_month:
            started = True
            v = vol.get(prev)
            share = b.LEV_MAX * (min(1.0, b.VOL_REF / v) if v and v > 0 else 1.0) * b.STAGE_B[stage]
            tgt_share = share
            if integer:
                cash += monthly_usd
                plan = fast_choose(q, cash, {"TQQQ": L[i], "SCHD": D[i]}, share, FEE)
                for s, n in plan.items():
                    px = L[i] if s == "TQQQ" else D[i]
                    if n < 0:
                        cash += -n * px * (1 - FEE)
                        q[s] += n
                for s, n in plan.items():
                    px = L[i] if s == "TQQQ" else D[i]
                    if n > 0:
                        cash -= n * px * (1 + FEE)
                        q[s] += n
            else:  # run_brake_184.simulate 와 같은 회계
                act_l, act_d = q["TQQQ"] * L[i], q["SCHD"] * D[i]
                active = act_l + act_d + monthly_usd
                tgt_l = share * active
                cash_m = monthly_usd
                if act_l > tgt_l:
                    n = (act_l - tgt_l) / L[i]
                    cash_m += n * L[i] * (1 - FEE)
                    q["TQQQ"] -= n
                tgt_d = active - tgt_l
                if act_d > tgt_d:
                    n = (act_d - tgt_d) / D[i]
                    cash_m += n * D[i] * (1 - FEE)
                    q["SCHD"] -= n
                need_l = max(tgt_l - q["TQQQ"] * L[i], 0.0)
                need_d = max(tgt_d - q["SCHD"] * D[i], 0.0)
                tot = need_l + need_d
                if tot > 0:
                    q["TQQQ"] += cash_m * need_l / tot * (1 - FEE) / L[i]
                    q["SCHD"] += cash_m * need_d / tot * (1 - FEE) / D[i]
                else:
                    q["SCHD"] += cash_m * (1 - FEE) / D[i]
        if started:
            l_max = max(l_max, L[i])
            val = q["TQQQ"] * L[i] + q["SCHD"] * D[i] + cash + res
            if i == len(days) - 1 or d[:7] != days[i + 1][:7]:
                rec.append((d[:7], val, cash / val if val else 0.0,
                            q["TQQQ"] * L[i] / val if val else 0.0, tgt_share))
    return rec


def nominal(path: list[float], sym: str) -> tuple[list[float], list[int]]:
    """수익률 경로(시작 100) → 시작 실제가 · 분할 규칙 반영 명목가와 그날 분할 배수(없으면 1).

    분할한 날 장부 주수를 × k 해야 평가액이 그대로다(엔진의 KL · KD)."""
    lim, k = SPLIT[sym]
    out, ks, f = [], [], START[sym] / path[0]
    for x in path:
        p, kk = x * f, 1
        if p >= lim:
            f /= k
            p, kk = x * f, k
        out.append(p)
        ks.append(kk)
    return out, ks


def validate(px, common) -> float:
    vol, tr = c1.signals(common, px["QQQ"])
    b.TAX = 0.0
    prev = b.LEV_MAX
    sim = b.simulate({"days": common, "lev": px["TQQQ"], "div": px["SCHD"], "vol": vol, "trend": tr,
                      "start": "2012-01-01"}, px["QQQ"], ref="T", monthly_krw=200_000)
    b.LEV_MAX = prev
    L = [px["TQQQ"][d] for d in common]
    D = [px["SCHD"][d] for d in common]
    rec = run(common, L, D, vol, tr, 200_000, False, "2012-01-01")
    return rec[-1][1] / sim["value_end"] - 1


def main() -> None:
    px = {s: c.load(R / f"data/research/div_mix_180/{s}.csv") for s in ["QQQ", "SCHD", "SPY", "IEF", "GLD"]}
    px["TQQQ"] = c.load(R / "data/research/lev_div_181/TQQQ.csv")
    common = sorted(d for d in set.intersection(*(set(v) for v in px.values())) if d <= "2026-08-31")
    bad = check_fast()
    print("fast_choose mismatches", bad, flush=True)
    assert bad == 0
    err = validate(px, common)
    print("validate frac vs simulate rel err", err, flush=True)
    assert abs(err) < 1e-9
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
    ends = {y: f"{2026 + y}-12" for y in HORIZONS}
    res: dict = {str(a): {"ratio": {str(y): [] for y in HORIZONS}, "idle_y1": [], "idle_all": [], "dev_y1": [], "dev_all": [],
                          "idle_y1_frac": []} for a in AMOUNTS}
    for n in range(NPATH):
        starts = rng.integers(0, len(rets) - 252 + 1, size=math.ceil((len(sd) - 1) / 252))
        rr = np.concatenate([rets[a:a + 252] for a in starts], axis=0)[: len(sd) - 1]
        pa = np.vstack([np.ones(6) * 100, 100 * np.cumprod(1 + rr, axis=0)])
        qqq = dict(zip(sd, pa[:, 0].tolist(), strict=True))
        vv, tt = c1.signals(sd, qqq)
        L, KL = nominal(pa[:, 1].tolist(), "TQQQ")
        D, KD = nominal(pa[:, 2].tolist(), "SCHD")
        for a in AMOUNTS:
            usd = a / FX
            fr = run(sd, L, D, vv, tt, usd, False, "2027-01-01", KL, KD)
            it = run(sd, L, D, vv, tt, usd, True, "2027-01-01", KL, KD)
            fm, im = {r[0]: r for r in fr}, {r[0]: r for r in it}
            r_ = res[str(a)]
            for y, e in ends.items():
                r_["ratio"][str(y)].append(im[e][1] / fm[e][1] - 1)
            y1 = [r for r in it if r[0] < "2028-01"]
            r_["idle_y1"].append(float(np.mean([r[2] for r in y1])))
            r_["idle_all"].append(float(np.mean([r[2] for r in it])))
            r_["dev_y1"].append(float(np.mean([abs(r[3] - r[4]) for r in y1])))
            r_["dev_all"].append(float(np.mean([abs(r[3] - r[4]) for r in it])))
            r_["idle_y1_frac"].append(float(np.mean([r[2] for r in fr if r[0] < "2028-01"])))
        if (n + 1) % 20 == 0:
            print("paths", n + 1, flush=True)
    out = {"fast_choose_mismatch": bad, "validate_rel_err": err, "paths": NPATH, "fx": FX, "start": START, "split": SPLIT, "summary": {}}
    for a in AMOUNTS:
        r_ = res[str(a)]
        out["summary"][str(a)] = {
            "ratio": {y: {k: float(np.quantile(v, q)) for k, q in [("p10", .1), ("p50", .5), ("p90", .9)]}
                      for y, v in r_["ratio"].items()},
            "idle_y1": float(np.mean(r_["idle_y1"])), "idle_all": float(np.mean(r_["idle_all"])),
            "dev_y1": float(np.mean(r_["dev_y1"])), "dev_all": float(np.mean(r_["dev_all"])),
        }
    (W / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
