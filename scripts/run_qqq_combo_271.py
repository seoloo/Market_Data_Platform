"""문서 271 — 나스닥 100 적립 + 고수익성 대형주(P) · 비트코인 5 · 10%(B5 · B10) (사전 등록 1 회).

python -m scripts.run_qqq_combo_271

계좌 규칙 · 나스닥 자료는 263 그대로(``run_qqq_combo_263.returns`` · ``account``).
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
import zipfile
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import scripts.run_qqq_combo_263 as m263

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "qqq_combo_271"
OUT = REPO / "reports" / "qqq_combo_271"
OP_ZIP = "6_Portfolios_ME_OP_2x3_CSV.zip"
SHA = {
    OP_ZIP: "5974ba70b4e23e46e58bdc821fb0c93caea86f46ca50523d0622e86d7f2eba5b",
    "CBBTCUSD.csv": "2b8ada9162cac28b2e1333276ac6e93cf60d39f57aa1042c0f18c348d3d4ba24",
}
P_MIX = {"Q": {"NDX": 1.0}, "P": {"NDX": 0.7, "OP": 0.3}}
B_MIX = {"Q": {"NDX": 1.0}, "B5": {"NDX": 0.95, "BTC": 0.05}, "B10": {"NDX": 0.9, "BTC": 0.1}}
B_FIRST, B_LAST_START, B_HALF, B_WIN = "2015-01", "2021-08", "2018-05", 60


def _check(name: str) -> Path:
    path = DATA / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != SHA[name]:
        raise SystemExit(f"{name} 해시 불일치")
    return path


def high_op() -> dict[str, float]:
    with zipfile.ZipFile(_check(OP_ZIP)) as z:
        lines = z.read(z.namelist()[0]).decode("latin-1").splitlines()
    out: dict[str, float] = {}
    ci = None
    for line in lines:
        parts = [x.strip() for x in line.split(",")]
        if ci is None:
            if "BIG HiOP" in parts:
                ci = parts.index("BIG HiOP")
            continue
        if parts[0].isdigit() and len(parts[0]) == 6:
            out[f"{parts[0][:4]}-{parts[0][4:]}"] = float(parts[ci]) / 100 - m263.SLEEVE_FEE / 12
        elif out:
            break  # 첫 표(가치 가중 월간)만
    return out


def btc() -> dict[str, float]:
    with _check("CBBTCUSD.csv").open(encoding="utf-8") as f:
        rows = [(r["observation_date"], r["CBBTCUSD"]) for r in csv.DictReader(f)]
    month_end: dict[str, float] = {}
    for d, v in rows:
        if v and v != ".":
            month_end[d[:7]] = float(v)
    ms = sorted(month_end)
    return {m: month_end[m] / month_end[p] - 1 for p, m in pairwise(ms)}


def drawdowns(months: Sequence[str], navs: Sequence[float]) -> dict[str, float]:
    """단위 가치 최대 낙폭 · 2022 낙폭 (263 것은 2000 · 2008 구간이 없으면 멈춘다)."""
    peak, mdd = navs[0], 0.0
    for n in navs:
        peak = max(peak, n)
        mdd = min(mdd, n / peak - 1)
    idx = [i for i, m in enumerate(months) if "2022-01" <= m <= "2022-12"]
    pk, worst = navs[idx[0]], 0.0
    for i in idx:
        pk = max(pk, navs[i])
        worst = min(worst, navs[i] / pk - 1)
    return {"mdd": mdd, "2022": worst}


def windows(
    months: Sequence[str],
    r: Mapping[str, Mapping[str, float]],
    mixes: Mapping[str, Mapping[str, float]],
    last_start: str,
    half: str,
    win: int,
) -> dict[str, Any]:
    full = {k: m263.account(months, r, w) for k, w in mixes.items()}
    starts = [m for m in months if m <= last_start]
    out: dict[str, Any] = {"months": [months[0], months[-1]], "windows": len(starts), "c": {}}
    base_full = full["Q"]["vals"][-1]
    for k, w in mixes.items():
        ratios: list[float] = []
        early: list[float] = []
        late: list[float] = []
        for s in starts:
            i = months.index(s)
            seg = months[i : i + win]
            a = m263.account(seg, r, w)["vals"][-1]
            b = m263.account(seg, r, mixes["Q"])["vals"][-1]
            ratios.append(a / b)
            (early if s < half else late).append(a / b)
        dd = drawdowns(months, full[k]["navs"])
        c = {
            "median": statistics.median(ratios),
            "p10": m263.pct(ratios, 0.1),
            "p90": m263.pct(ratios, 0.9),
            "win_share": sum(1 for x in ratios if x > 1) / len(ratios),
            "median_early": statistics.median(early),
            "median_late": statistics.median(late),
            "full_ratio": full[k]["vals"][-1] / base_full,
            "mdd": dd["mdd"],
            "dd_2022": dd["2022"],
        }
        if k != "Q":
            ok = (
                c["median"] >= 1
                and c["win_share"] >= 0.6
                and c["median_early"] >= 1
                and c["median_late"] >= 1
                and c["full_ratio"] >= 1
            )
            c["verdict"] = "HISTORICAL_SUPPORT" if ok else "REJECT"
        out["c"][k] = c
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    months, r = m263.returns()
    r = {**r, "OP": high_op(), "BTC": btc()}
    p_months = [m for m in months if m in r["OP"]]
    res: dict[str, Any] = {"sha": SHA}
    res["P"] = windows(p_months, r, P_MIX, m263.LAST_START, m263.HALF, m263.WIN)
    b_months = [m for m in months if m >= B_FIRST and m in r["BTC"]]
    res["B"] = windows(b_months, r, B_MIX, B_LAST_START, B_HALF, B_WIN)
    for k in ("B5", "B10"):
        c = res["B"]["c"][k]
        c["verdict"] = c["verdict"].replace("HISTORICAL_SUPPORT", "HISTORICAL_SUPPORT(사후 선택)")
        w = B_MIX[k]["BTC"]
        h = {"NDX": 0.0, "BTC": 0.0}
        top = 0.0
        for m in b_months:  # 한 번 적립 계좌에서 비트코인 몫이 가장 컸을 때 (보고)
            v = sum(h.values()) + 1
            need = {a: max(0.0, v * t - h[a]) for a, t in B_MIX[k].items()}
            tot = sum(need.values()) or 1.0
            for a in h:
                h[a] = (h[a] + need[a] / tot) * (1 + r[a][m])
            if m[5:] == "12":
                s = sum(h.values())
                h = {"NDX": s * (1 - w), "BTC": s * w}
            top = max(top, h["BTC"] / sum(h.values()))
        c["max_btc_share"] = top
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main() -> int:
    res = run()
    for part in ("P", "B"):
        print(part, res[part]["months"], "창", res[part]["windows"])
        for k, c in res[part]["c"].items():
            print(
                f" {k:4} 중앙 {c['median']:.3f} (10% {c['p10']:.3f} 90% {c['p90']:.3f})"
                f" 이김 {c['win_share']:.0%} 앞 {c['median_early']:.3f} 뒤 {c['median_late']:.3f}"
                f" 한번 {c['full_ratio']:.3f} 낙폭 {c['mdd']:.1%} 22 {c['dd_2022']:.0%}"
                f" {c.get('verdict', '')} {c.get('max_btc_share', '')}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
