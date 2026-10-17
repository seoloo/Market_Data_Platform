"""나무 안정 계좌 후보 A · B · C vs 🌟 Deneb — 참고 계산(판정 아님).

사용자 (2026-10-17): "A, B, C 그리고 deneb와 같이 비교해줘".

실행 전에 정한 것 (바꾸지 않는다):

* A = S&P 500 100% (SPY 를 VOO 대신) · B = S&P 500 70 + 나스닥 100 30 (QQQ 를 QQQM 대신) ·
  C = S&P 500 60 + 미국 7 ~ 10 년 국채 20 (IEF) + 금 20 (GLD 를 GLDM 대신) ·
  D = Deneb(M3): TQQQ 60% × min(1, 0.20 / QQQ 20 거래일 변동성), 나머지 SCHD. 브레이크는 실거래에
  안 붙어 있어 넣지 않는다.
* 매달 첫 거래일에 목표 비율로 다시 맞춘다(비율은 전 거래일 종가까지로). 사고판 금액 × 0.09%
  (나무 해외 수수료) 를 뺀다. 환전 비용 · 세금 · 환율은 넣지 않는다(네 후보 모두 달러 자산).
* 기간 ① 진짜 ETF 만: 2011-11 ~ 2026-09 (SCHD 상장 뒤). ② 긴 기간: 2005-01 ~ 2026-09 (2008 포함) —
  TQQQ 상장 전은 QQQ 일간 3 배 − 연 비용(겹치는 2010-02 ~ 2026-09 총수익이 같아지게 맞춤),
  SCHD 상장 전은 DVY 로 잇는다.
* 지표: 한 번에 넣기(연 수익 · 변동성 · 최대 낙폭 · 회복 기간 · 최악 · 최고 해 · 2008 · 2020 ·
  2022) +
  매달 같은 돈 넣기(넣은 돈 대비 최종 배수 · 넣은 돈보다 가장 많이 밑돈 때).

자료: ``data/research/div_mix_180`` (SPY · QQQ · IEF · GLD · SCHD · DVY) · ``lev_div_181`` (TQQQ),
수정 종가. 결과 ``reports/namu_core_compare/result.json``.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import statistics
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research"
OUT = REPO / "reports" / "namu_core_compare"
FEE = 0.0009
LEV_MAX, VOL_REF = 0.6, 0.20
WINDOWS = {"real_2011": ("2011-11-01", "2026-09-30"), "long_2005": ("2005-01-01", "2026-09-30")}

Series = dict[str, float]


def load(path: Path) -> Series:
    with path.open(encoding="utf-8") as f:
        return {r["date"]: float(r["adjclose"]) for r in csv.DictReader(f) if r["adjclose"]}


def rets(s: Series, days: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for a, b in itertools.pairwise(days):
        if a in s and b in s:
            out[b] = s[b] / s[a] - 1
    return out


def fit_drag(qqq_r: Mapping[str, float], real: Series, days: list[str]) -> float:
    """3 배 합성의 연 비용 — 겹치는 기간 총수익이 진짜 TQQQ 와 같아지게(이분법)."""
    span = [d for d in days if d in real]
    target = real[span[-1]] / real[span[0]]

    def total(drag: float) -> float:
        v = 1.0
        for d in span[1:]:
            v *= 1 + 3 * qqq_r[d] - drag / 252
        return v

    lo, hi = -0.2, 0.5
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if total(mid) > target else (lo, mid)
    return (lo + hi) / 2


def chain(real: Series, proxy_r: Mapping[str, float], days: list[str]) -> Series:
    """진짜가 있으면 진짜 수익, 없으면 대신 수익으로 이은 가격."""
    first = next(d for d in days if d in real)
    out: Series = {first: real[first]}
    i0 = days.index(first)
    for k in range(i0 - 1, -1, -1):  # 뒤로
        nxt = days[k + 1]
        if nxt not in proxy_r:
            break
        out[days[k]] = out[nxt] / (1 + proxy_r[nxt])
    for a, b in zip(days[i0:], days[i0 + 1 :], strict=False):
        out[b] = real[b] if b in real else out[a]
    return out


def vol20(qqq: Series, days: list[str], i: int) -> float | None:
    if i < 21:
        return None
    xs = [qqq[d] for d in days[i - 21 : i]]
    r = [b / a - 1 for a, b in itertools.pairwise(xs)]
    return statistics.pstdev(r) * math.sqrt(252)


Rule = Callable[[int], dict[str, float]]


def simulate(
    px: Mapping[str, Series], days: list[str], rule: Rule, i0: int, i1: int, *, monthly: float
) -> tuple[list[float], list[float]]:
    """(평가액, 넣은 돈 누계) 매일. ``monthly`` = 0 이면 처음 1 만 넣기."""
    hold: dict[str, float] = {}
    vals, paid = [], []
    put = 0.0
    for i in range(i0, i1 + 1):
        d = days[i]
        value = sum(q * px[s][d] for s, q in hold.items())
        if i == i0 or d[:7] != days[i - 1][:7]:
            add = 1.0 if i == i0 else monthly
            value += add
            put += add
            w = rule(i)
            new = {s: value * w[s] / px[s][d] for s in w if w[s] > 0}
            turn = sum(
                abs(new.get(s, 0.0) - hold.get(s, 0.0)) * px[s][d] for s in set(new) | set(hold)
            )
            cost = turn * FEE
            scale = (value - cost) / value
            hold = {s: q * scale for s, q in new.items()}
            value -= cost
        vals.append(value)
        paid.append(put)
    return vals, paid


def stats(days: list[str], vals: list[float]) -> dict[str, object]:
    n = len(vals)
    years = n / 252
    cagr = (vals[-1] / vals[0]) ** (1 / years) - 1
    r = [b / a - 1 for a, b in itertools.pairwise(vals)]
    vol = statistics.pstdev(r) * math.sqrt(252)
    peak, mdd, mdd_day, under, longest, start_under = vals[0], 0.0, days[0], 0, 0, 0
    for k, v in enumerate(vals):
        if v >= peak:
            peak = v
            longest = max(longest, k - start_under) if under else longest
            under = 0
        else:
            if not under:
                start_under = k
            under = 1
            if v / peak - 1 < mdd:
                mdd, mdd_day = v / peak - 1, days[k]
    if under:
        longest = max(longest, n - 1 - start_under)
    by_year: dict[str, float] = {}
    last: dict[str, float] = {}
    for d, v in zip(days, vals, strict=True):
        last[d[:4]] = v
    prev = vals[0]
    for y in sorted(last):
        by_year[y] = last[y] / prev - 1
        prev = last[y]
    return {
        "cagr": cagr,
        "vol": vol,
        "mdd": mdd,
        "mdd_day": mdd_day,
        "underwater_months": round(longest / 21, 1),
        "worst_year": min(by_year.items(), key=lambda x: x[1]),
        "best_year": max(by_year.items(), key=lambda x: x[1]),
        "y2008": by_year.get("2008"),
        "y2020": by_year.get("2020"),
        "y2022": by_year.get("2022"),
        "growth": vals[-1] / vals[0],
    }


def dca_stats(vals: list[float], paid: list[float]) -> dict[str, float]:
    return {
        "multiple": vals[-1] / paid[-1],
        "worst_vs_paid": min(v / p - 1 for v, p in zip(vals, paid, strict=True)),
        "months": paid[-1],
    }


def main() -> int:
    raw = {
        s: load(DATA / "div_mix_180" / f"{s}.csv")
        for s in ("SPY", "QQQ", "IEF", "GLD", "SCHD", "DVY")
    }
    raw["TQQQ"] = load(DATA / "lev_div_181" / "TQQQ.csv")
    base = ("SPY", "QQQ", "IEF", "GLD", "DVY")
    days = sorted(set.intersection(*(set(raw[s]) for s in base)))
    days = [d for d in days if "2004-12-01" <= d <= "2026-09-30"]
    qqq_r = rets(raw["QQQ"], days)
    drag = fit_drag(qqq_r, raw["TQQQ"], days)
    syn_t = {d: 3 * r - drag / 252 for d, r in qqq_r.items()}
    px: dict[str, Series] = {s: raw[s] for s in base}
    px["TQQQ"] = chain(raw["TQQQ"], syn_t, days)
    px["SCHD"] = chain(raw["SCHD"], rets(raw["DVY"], days), days)

    def deneb(i: int) -> dict[str, float]:
        v = vol20(px["QQQ"], days, i)
        share = LEV_MAX if v is None or v <= 0 else LEV_MAX * min(1.0, VOL_REF / v)
        return {"TQQQ": share, "SCHD": 1 - share}

    rules: dict[str, Rule] = {
        "A": lambda i: {"SPY": 1.0},
        "B": lambda i: {"SPY": 0.7, "QQQ": 0.3},
        "C": lambda i: {"SPY": 0.6, "IEF": 0.2, "GLD": 0.2},
        "Deneb": deneb,
    }
    out: dict[str, object] = {"tqqq_synthetic_drag": drag}
    for wname, (a, b) in WINDOWS.items():
        i0 = next(k for k, d in enumerate(days) if d >= a)
        i1 = max(k for k, d in enumerate(days) if d <= b)
        res: dict[str, object] = {"from": days[i0], "to": days[i1]}
        for name, rule in rules.items():
            vals, _ = simulate(px, days, rule, i0, i1, monthly=0.0)
            dv, dp = simulate(px, days, rule, i0, i1, monthly=1.0)
            res[name] = {"lump": stats(days[i0 : i1 + 1], vals), "dca": dca_stats(dv, dp)}
        out[wname] = res
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"TQQQ 합성 연 비용 {drag:.2%}")
    for wname in WINDOWS:
        res = out[wname]
        assert isinstance(res, dict)
        print(f"\n== {wname} {res['from']} ~ {res['to']}")
        for name in rules:
            lump, dca = res[name]["lump"], res[name]["dca"]
            print(
                f"{name:6} 연 {lump['cagr']:+.1%} 변동 {lump['vol']:.1%} 낙폭 {lump['mdd']:.1%}"
                f"({lump['mdd_day']}) 회복 {lump['underwater_months']}달"
                f" 최악 {lump['worst_year'][0]} {lump['worst_year'][1]:+.1%}"
                f" 최고 {lump['best_year'][0]} {lump['best_year'][1]:+.1%}"
                f" 08 {lump['y2008'] if lump['y2008'] is None else format(lump['y2008'], '+.1%')}"
                f" 20 {lump['y2020']:+.1%} 22 {lump['y2022']:+.1%} 배 {lump['growth']:.2f}"
                f" | 적립 배수 {dca['multiple']:.2f} 최악 {dca['worst_vs_paid']:+.1%}"
            )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
