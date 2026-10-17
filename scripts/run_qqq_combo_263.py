"""문서 263 — 나스닥 100 + 한 자산 섞기 (사전 등록 f5c8bc6).

사용 예::

    python -m scripts.run_qqq_combo_263   # 한 번 → reports/qqq_combo_263/result.json

월 자료(1986 ~ 2026-08)로 10 년 매달 적립 창을 돌려 후보 ÷ 나스닥만 끝 금액 비율을 본다.
새 돈은 모자란 자산부터, 매년 12 월 말 목표 비율로 맞춤, 비용 편도 0.15%, 세전.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import scripts.run_multi_asset_176 as m176
import scripts.run_namu_core_compare as nc

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "qqq_combo_263"
OUT = REPO / "reports" / "qqq_combo_263"
COST = 0.0015
NDX_DIV = 0.008
SLEEVE_FEE = 0.0025
FIRST, LAST_START, END = "1986-02", "2016-09", "2026-08"
WIN = 120
HALF = "2001-06"
FRENCH = {
    "MOM": ("6_Portfolios_ME_Prior_12_2_CSV.zip", "BIG HiPRIOR"),
    "SV": ("6_Portfolios_2x3_CSV.zip", "SMALL HiBM"),
}
FRENCH_SHA = {
    "6_Portfolios_ME_Prior_12_2_CSV.zip": "68d90a218235b2c14e67eb1b7b1acd2d9fcc6b626bc4c64e854e2788bcf44bf3",
    "6_Portfolios_2x3_CSV.zip": "82b66ccd6a4dff103eba51b60e1f2310eaa94bbebbb843550173b972c64cdbff",
}
MIXES: dict[str, dict[str, float]] = {
    "Q": {"NDX": 1.0},
    "G": {"NDX": 0.8, "GOLD": 0.2},
    "B": {"NDX": 0.8, "UST10": 0.2},
    "M": {"NDX": 0.7, "MOM": 0.3},
    "V": {"NDX": 0.7, "SV": 0.3},
}
CRISES = {"2000~02": ("2000-01", "2002-12"), "2008": ("2007-10", "2009-03"), "2022": ("2022-01", "2022-12")}


def french(name: str, col: str) -> dict[str, float]:
    path = DATA / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != FRENCH_SHA[name]:
        raise SystemExit(f"{name} 해시 불일치")
    with zipfile.ZipFile(path) as z:
        lines = z.read(z.namelist()[0]).decode("latin-1").splitlines()
    out: dict[str, float] = {}
    ci = None
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if ci is None:
            if col in parts:
                ci = parts.index(col)
            continue
        if parts[0].isdigit() and len(parts[0]) == 6:
            out[f"{parts[0][:4]}-{parts[0][4:]}"] = float(parts[ci]) / 100
        elif out:
            break  # 첫 표(가치 가중 월간)만
    return out


def returns() -> tuple[list[str], dict[str, dict[str, float]]]:
    if m176.data_hash() != m176.EXPECTED_DATA_SHA:
        raise SystemExit("176 자료 해시 불일치")
    table = m176.build_table()
    r: dict[str, dict[str, float]] = {"NDX": {}, "GOLD": {}, "UST10": {}}
    for m, row in table.items():
        for k in r:
            if k in row:
                r[k][m] = row[k] + (NDX_DIV / 12 if k == "NDX" else 0.0)
    for k, (name, col) in FRENCH.items():
        r[k] = {m: v - SLEEVE_FEE / 12 for m, v in french(name, col).items()}
    months = sorted(m for m in set.intersection(*(set(v) for v in r.values())) if FIRST <= m <= END)
    return months, r


def account(months: Sequence[str], r: Mapping[str, Mapping[str, float]], w: Mapping[str, float]) -> dict[str, Any]:
    """매달 초 1 단위 납입(모자란 것부터) → 그달 수익 → 12 월 말 맞춤."""
    h = dict.fromkeys(w, 0.0)
    units = 0.0
    vals: list[float] = []
    navs: list[float] = []
    for m in months:
        value = sum(h.values())
        nav = value / units if units else 1.0
        units += 1 / nav
        spend = 1 / (1 + COST)
        need = {a: max(0.0, (value + 1) * t - h[a]) for a, t in w.items()}
        tot = sum(need.values())
        buy = {a: spend * need[a] / tot for a in w} if tot >= spend else {
            a: need[a] + (spend - tot) * t for a, t in w.items()}
        for a in w:
            h[a] = (h[a] + buy[a]) * (1 + r[a][m])
        if m[5:] == "12" and len(w) > 1:
            total = sum(h.values())
            turn = sum(abs(total * t - h[a]) for a, t in w.items())
            scale = 1 - turn * COST / total
            h = {a: total * t * scale for a, t in w.items()}
        vals.append(sum(h.values()))
        navs.append(vals[-1] / units)
    return {"vals": vals, "navs": navs}


def drawdowns(months: Sequence[str], navs: Sequence[float]) -> dict[str, float]:
    peak, mdd = navs[0], 0.0
    for n in navs:
        peak = max(peak, n)
        mdd = min(mdd, n / peak - 1)
    out = {"mdd": mdd}
    for name, (a, b) in CRISES.items():
        idx = [i for i, m in enumerate(months) if a <= m <= b]
        pk, worst = navs[idx[0]], 0.0
        for i in idx:
            pk = max(pk, navs[i])
            worst = min(worst, navs[i] / pk - 1)
        out[name] = worst
    return out


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[int(q * (len(s) - 1))]


def etf_check() -> dict[str, Any]:
    """2005-01 ~ 2026-08 실제 ETF, 매달 첫 거래일 20 만 원, 12 월 마지막 거래일 맞춤 (보고만)."""
    px = {s: nc.load(nc.DATA / "div_mix_180" / f"{s}.csv") for s in ("QQQ", "GLD", "IEF")}
    days = sorted(d for d in set.intersection(*(set(v) for v in px.values())) if "2004-12-01" <= d <= "2026-08-31")
    sets = {"Q": {"QQQ": 1.0}, "G": {"QQQ": 0.8, "GLD": 0.2}, "B": {"QQQ": 0.8, "IEF": 0.2}}
    out: dict[str, Any] = {}
    for name, w in sets.items():
        q = dict.fromkeys(w, 0.0)
        units = 0.0
        navs: list[float] = []
        mons: list[str] = []
        value = 0.0
        for i, d in enumerate(days):
            if d < "2005-01-01":
                continue
            p = {a: px[a][d] for a in w}
            value = sum(q[a] * p[a] for a in w)
            if d[:7] != days[i - 1][:7]:
                nav = value / units if units else 1.0
                units += 200_000 / nav
                spend = 200_000 / (1 + COST)
                need = {a: max(0.0, (value + 200_000) * t - q[a] * p[a]) for a, t in w.items()}
                tot = sum(need.values())
                buy = {a: spend * need[a] / tot for a in w} if tot >= spend else {
                    a: need[a] + (spend - tot) * t for a, t in w.items()}
                for a in w:
                    q[a] += buy[a] / p[a]
                value = sum(q[a] * p[a] for a in w)
            last_dec = d[5:7] == "12" and (i + 1 == len(days) or days[i + 1][:7] != d[:7])
            if last_dec and len(w) > 1:
                turn = sum(abs(value * t - q[a] * p[a]) for a, t in w.items())
                scale = 1 - turn * COST / value
                q = {a: value * t * scale / p[a] for a, t in w.items()}
                value = sum(q[a] * p[a] for a in w)
            navs.append(value / units)
            mons.append(d[:7])
        peak, mdd = navs[0], 0.0
        for n in navs:
            peak = max(peak, n)
            mdd = min(mdd, n / peak - 1)
        out[name] = {"final_krw": value, "paid_krw": 200_000 * len(set(mons)), "mdd": mdd}
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    months, r = returns()
    full = {k: account(months, r, w) for k, w in MIXES.items()}
    starts = [m for m in months if m <= LAST_START]
    res: dict[str, Any] = {
        "prereg": "f5c8bc6",
        "months": [months[0], months[-1]],
        "french_sha": FRENCH_SHA,
        "windows": len(starts),
        "candidates": {},
    }
    base_full = full["Q"]["vals"][-1]
    for k, w in MIXES.items():
        ratios, early, late = [], [], []
        for s in starts:
            i = months.index(s)
            win = months[i : i + WIN]
            a = account(win, r, w)["vals"][-1]
            b = account(win, r, MIXES["Q"])["vals"][-1]
            ratios.append(a / b)
            (early if s < HALF else late).append(a / b)
        dd = drawdowns(months, full[k]["navs"])
        c = {
            "median": statistics.median(ratios),
            "p10": pct(ratios, 0.1),
            "p90": pct(ratios, 0.9),
            "win_share": sum(1 for x in ratios if x > 1) / len(ratios),
            "median_early": statistics.median(early),
            "median_late": statistics.median(late),
            "full_final": full[k]["vals"][-1],
            "full_ratio": full[k]["vals"][-1] / base_full,
            **dd,
        }
        if k != "Q":
            c["verdict"] = (
                "HISTORICAL_SUPPORT"
                if c["median"] >= 1 and c["win_share"] >= 0.6 and c["median_early"] >= 1
                and c["median_late"] >= 1 and c["full_ratio"] >= 1
                else "REJECT"
            )
        res["candidates"][k] = c
    res["etf_2005"] = etf_check()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main() -> int:
    res = run()
    print(res["months"], "창", res["windows"])
    for k, c in res["candidates"].items():
        print(
            f"{k} 중앙 {c['median']:.3f} (10% {c['p10']:.3f} 90% {c['p90']:.3f}) 이김 {c['win_share']:.0%}"
            f" 앞 {c['median_early']:.3f} 뒤 {c['median_late']:.3f} 전체 {c['full_ratio']:.3f}"
            f" 낙폭 {c['mdd']:.1%} 닷컴 {c['2000~02']:.0%} 08 {c['2008']:.0%} 22 {c['2022']:.0%}"
            f" {c.get('verdict', '')}"
        )
    for k, e in res["etf_2005"].items():
        print(f"ETF {k} 끝 {e['final_krw'] / 1e4:,.0f}만 (원금 {e['paid_krw'] / 1e4:,.0f}만) 낙폭 {e['mdd']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
