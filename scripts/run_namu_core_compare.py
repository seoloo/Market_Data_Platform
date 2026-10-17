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

``lev`` (사용자 2026-10-17 "C로 해서 sp500 1배 2배 3배 각각 비교해줘", 실행 전 고정):
C 의 S&P 500 60% 를 C1 SPY(1 배) · C2 SSO(2 배) · C3 UPRO(3 배)로 바꾼다. 국채 20 · 금 20 · 매달
다시 맞추기 · 수수료 · 기간 · 지표는 위와 같다.
상장 전(SSO 2006-06 · UPRO 2009-06)은 SPY 일간 2 · 3 배
− 연 비용(겹치는 기간 총수익이 같아지게 맞춤)으로 잇는다. 참고로 Deneb 줄을 같이 둔다.
자료 ``data/research/namu_core_compare`` (SSO · UPRO, 야후 수정 종가 2026-10-17 받음).
결과 ``reports/namu_core_compare/result_lev.json``.

``lev6`` (사용자 2026-10-17 "sp500 말고 나스닥으로 1배 2배 3배 sp500 1배 2배 3배 비교",
실행 전 고정):
주식 60 · 국채 20 · 금 20 에서 주식을 S1 SPY · S2 SSO · S3 UPRO · N1 QQQ · N2 QLD · N3 TQQQ 로.
QLD 상장 전(2006-06)은 QQQ 일간 2 배 − 연 비용(겹치는 기간 맞춤). 나머지는 ``lev`` 와 같다.
결과 ``reports/namu_core_compare/result_lev6.json``.

``half`` (사용자 2026-10-17 "S&P 30 + 나스닥 30 + 국채 20 + 금 20 이거는", 실행 전 고정):
H = SPY 30 · QQQ 30 · IEF 20 · GLD 20 을 S1 · N1 과 나란히. 조건은 위와 같다.
결과 ``reports/namu_core_compare/result_half.json``.

``dca20`` (사용자 2026-10-17 "polaris로 20년 동안 20만원씩 입금하면 얼마가 되는 지 … deneb랑 비교",
실행 전 고정): Polaris = SPY 30 · QQQ 30 · IEF 20 · GLD 20 (SPYM · QQQM · GLDM 대신),
Deneb 은 위와 같다.
① 실제 최근 20 년: 2006-10 ~ 2026-09 매달 첫 거래일에 20 만 원(달러 자산, 환율 고정 가정).
② 미래 범위: 2005-01 ~ 2026-09 월 수익(매달 다시 맞춤 · 수수료 포함)을 12 달 묶음으로 무작위
   다시 뽑아 20 년 1 만 번(씨앗 20261017, 두 전략 같은 묶음) → 10 · 50 · 90 백분위와 Polaris 가
   Deneb 보다 많은 비율, 중간에 넣은 돈보다 가장 많이 밑돈 정도. 세금 · 환율 · 환전 비용 없음.
결과 ``reports/namu_core_compare/result_dca20.json``.

``rebal`` (사용자 2026-10-17 "세 가지 같은 조건으로 계산", 실행 전 고정):
Polaris(SPY 30 · QQQ 30 · IEF 20 · GLD 20) 의 비율 맞추기 세 가지 + 참고 하나.
BUY = 사기만(새 돈을 모자란 종목부터, 팔지 않음) · YEAR = BUY + 매년 12 월 첫 거래일에 목표로 다시
맞춤(팔기 포함) · BAND = BUY + 매달 첫 거래일에 어느 종목이든 목표에서 5%p 넘게 벗어나면 목표로
다시 맞춤 · 참고 MONTH = 매달 다시 맞춤(앞 계산들의 가정).
돈 넣기 두 가지: ① 100 만 원 × 4 달 뒤 안 넣음(사용자 계획) · ② 매달 20 만 원 계속.
기간: 2005-01 ~ 2026-09 · 2011-11 ~ 2026-09. 수수료 0.09%, 소수 주 허용, 환율 고정(달러 = 원 비율).
양도세: 판 해의 실현 이익(평균 단가) − 250 만 원 × 22% 를 다음 해 첫 거래일에 평가액에서 뺀다
(끝에 다 파는 세금은 넣지 않음 — 안 판 이익은 BUY 가 가장 큼).
지표: 끝 평가액, 단위 가치 최대 낙폭 ·
2008 · 2020 · 2022, 주식(SPY + QQQ) 비중 최고, 다시 맞춘 횟수, 낸 세금, 끝의 안 판 이익.
결과 ``reports/namu_core_compare/result_rebal.json``.

``split`` (사용자 2026-10-17 "4개월 100만원씩이 좋을까 8개월 50만원씩이 좋을까", 실행 전 고정):
Polaris 단위 가치(매달 다시 맞춤, 앞과 같음)에 400 만 원을 4 달 × 100 만 vs 8 달 × 50 만
(매달 첫 거래일).
아직 안 넣은 돈은 이자 0. 시작 달을 2005-01 부터 24 달 뒤 자료가 있는 마지막 달까지 하나씩 옮겨 가며
12 · 24 달 뒤 평가액, 그 24 달 동안 '평가액 − 넣은 돈' 최저(원), 4 달 쪽이 더 많은 비율 ·
평균 차이 ·
하위 10% · 가장 나빴던 시작 달.
결과 ``reports/namu_core_compare/result_split.json``.
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
from typing import Any

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


def fit_drag(qqq_r: Mapping[str, float], real: Series, days: list[str], mult: float = 3.0) -> float:
    """``mult`` 배 합성의 연 비용 — 겹치는 기간 총수익이 진짜 ETF 와 같아지게(이분법)."""
    span = [d for d in days if d in real]
    target = real[span[-1]] / real[span[0]]

    def total(drag: float) -> float:
        v = 1.0
        for d in span[1:]:
            v *= 1 + mult * qqq_r[d] - drag / 252
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


def POLARIS(i: int) -> dict[str, float]:  # noqa: N802 - 전략 이름
    return {"SPY": 0.3, "QQQ": 0.3, "IEF": 0.2, "GLD": 0.2}


def dca20(px: Mapping[str, Series], days: list[str], rules: Mapping[str, Rule]) -> int:
    """20 년 매달 20 만 원 — 실제 최근 20 년 + 12 달 묶음 다시 뽑기."""
    import random

    monthly_krw, years = 200_000, 20
    out: dict[str, Any] = {"paid": monthly_krw * 12 * years}
    i0 = next(k for k, d in enumerate(days) if d >= "2006-10-01")
    i1 = max(k for k, d in enumerate(days) if d <= "2026-09-30")
    real: dict[str, Any] = {}
    for name, rule in rules.items():
        vals, paid = simulate(px, days, rule, i0, i1, monthly=1.0)
        real[name] = {
            "final": vals[-1] * monthly_krw,
            "worst_vs_paid": min(v / p - 1 for v, p in zip(vals, paid, strict=True)),
        }
    out["real_2006_2026"] = real
    j0 = next(k for k, d in enumerate(days) if d >= "2005-01-01")
    month_ret: dict[str, list[float]] = {}
    for name, rule in rules.items():
        vals, _ = simulate(px, days, rule, j0, i1, monthly=0.0)
        ends = [
            v
            for k, v in enumerate(vals)
            if k + j0 == i1 or days[k + j0][:7] != days[k + j0 + 1][:7]
        ]
        month_ret[name] = [b / a - 1 for a, b in itertools.pairwise([vals[0], *ends])]
    n = min(len(r) for r in month_ret.values())
    rng = random.Random(20261017)
    finals: dict[str, list[float]] = {k: [] for k in rules}
    worsts: dict[str, list[float]] = {k: [] for k in rules}
    wins = 0
    for _ in range(10_000):
        starts = [rng.randrange(0, n - 12) for _ in range(years)]
        path_final = {}
        for name in rules:
            r = month_ret[name]
            v = put = 0.0
            worst = 0.0
            for s0 in starts:
                for m in range(12):
                    v = (v + 1) * (1 + r[s0 + m])
                    put += 1
                    worst = min(worst, v / put - 1)
            finals[name].append(v * monthly_krw)
            worsts[name].append(worst)
            path_final[name] = v
        wins += path_final["Polaris"] > path_final["Deneb"]

    def pct(xs: list[float], q: float) -> float:
        ys = sorted(xs)
        return ys[int(q * (len(ys) - 1))]

    out["bootstrap"] = {
        name: {
            "p10": pct(finals[name], 0.10),
            "p50": pct(finals[name], 0.50),
            "p90": pct(finals[name], 0.90),
            "worst_vs_paid_p50": pct(worsts[name], 0.50),
            "worst_vs_paid_p10": pct(worsts[name], 0.10),
            "below_paid_at_end": sum(f < out["paid"] for f in finals[name]) / len(finals[name]),
        }
        for name in rules
    }
    out["bootstrap"]["polaris_beats_deneb"] = wins / 10_000
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result_dca20.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


TARGET = {"SPY": 0.3, "QQQ": 0.3, "IEF": 0.2, "GLD": 0.2}
STOCKS = ("SPY", "QQQ")
BAND = 0.05
TAX_FREE, TAX_RATE = 2_500_000.0, 0.22


def run_policy(
    px: Mapping[str, Series], days: list[str], i0: int, i1: int, policy: str, deposit: Rule0
) -> dict[str, Any]:
    """원 단위(환율 고정). ``deposit(k)`` = k 번째 달에 넣는 원."""
    q = dict.fromkeys(TARGET, 0.0)
    basis = dict.fromkeys(TARGET, 0.0)
    cash = units = paid = 0.0
    realized: dict[str, float] = {}
    tax_total = 0.0
    navs: list[float] = []
    vals: list[float] = []
    max_stock, rebals, k = 0.0, 0, -1
    for i in range(i0, i1 + 1):
        d = days[i]
        price = {s: px[s][d] for s in TARGET}
        value = cash + sum(q[s] * price[s] for s in TARGET)
        if i > i0 and d[:4] != days[i - 1][:4]:  # 해가 바뀜 → 지난해 세금
            gain = realized.get(days[i - 1][:4], 0.0)
            tax = max(0.0, gain - TAX_FREE) * TAX_RATE
            if tax > 0 and value > 0:
                scale = (value - tax) / value
                q = {s: v * scale for s, v in q.items()}
                basis = {s: v * scale for s, v in basis.items()}
                cash *= scale
                value -= tax
                tax_total += tax
        if i == i0 or d[:7] != days[i - 1][:7]:
            k += 1
            add = deposit(k)
            nav = value / units if units else 1.0
            units += add / nav
            cash += add
            paid += add
            value += add
            w = {s: q[s] * price[s] / value for s in TARGET} if value else dict(TARGET)
            full = (
                policy == "MONTH"
                or (policy == "YEAR" and d[5:7] == "12" and days[i - 1][5:7] != "12")
                or (policy == "BAND" and any(abs(w[s] - t) > BAND for s, t in TARGET.items()))
            ) and sum(q.values()) > 0
            if full:
                rebals += 1
                want = {s: value * t / price[s] for s, t in TARGET.items()}
                turn = sum(abs(want[s] - q[s]) * price[s] for s in TARGET)
                fee = turn * FEE
                for s in TARGET:
                    if want[s] < q[s]:
                        sold = q[s] - want[s]
                        avg = basis[s] / q[s] if q[s] else 0.0
                        realized[d[:4]] = realized.get(d[:4], 0.0) + sold * (price[s] - avg)
                        basis[s] -= sold * avg
                    else:
                        basis[s] += (want[s] - q[s]) * price[s]
                scale = (value - fee) / value
                q = {s: want[s] * scale for s in TARGET}
                basis = {s: basis[s] for s in TARGET}
                cash = 0.0
                value -= fee
            elif cash > 0:
                spend = cash / (1 + FEE)
                need = {s: max(0.0, value * t - q[s] * price[s]) for s, t in TARGET.items()}
                tot = sum(need.values())
                if tot >= spend:
                    buy = {s: spend * need[s] / tot for s in TARGET}
                else:
                    buy = {s: need[s] + (spend - tot) * t for s, t in TARGET.items()}
                for s in TARGET:
                    q[s] += buy[s] / price[s]
                    basis[s] += buy[s]
                value -= cash - spend
                cash = 0.0
        stock = sum(q[s] * price[s] for s in STOCKS)
        if value > 0 and sum(q.values()) > 0:
            max_stock = max(max_stock, stock / value)
        vals.append(value)
        navs.append(value / units if units else 1.0)
    st = stats(days[i0 : i1 + 1], navs)
    return {
        "final": vals[-1],
        "paid": paid,
        "mdd": st["mdd"],
        "mdd_day": st["mdd_day"],
        "y2008": st["y2008"],
        "y2020": st["y2020"],
        "y2022": st["y2022"],
        "cagr_twr": st["cagr"],
        "max_stock_weight": max_stock,
        "end_stock_weight": sum(q[s] * px[s][days[i1]] for s in STOCKS) / vals[-1],
        "rebalances": rebals,
        "tax_paid": tax_total,
        "unrealized_end": vals[-1] - sum(basis.values()),
    }


Rule0 = Callable[[int], float]


def rebal(px: Mapping[str, Series], days: list[str]) -> int:
    plans: dict[str, Rule0] = {
        "plan_4x100": lambda k: 1_000_000.0 if k < 4 else 0.0,
        "monthly_20": lambda k: 200_000.0,
    }
    out: dict[str, Any] = {}
    for wname, (a, b) in WINDOWS.items():
        i0 = next(k for k, d in enumerate(days) if d >= a)
        i1 = max(k for k, d in enumerate(days) if d <= b)
        for pname, dep in plans.items():
            res = {
                pol: run_policy(px, days, i0, i1, pol, dep)
                for pol in ("BUY", "YEAR", "BAND", "MONTH")
            }
            out[f"{wname}/{pname}"] = res
            print(f"== {wname} {pname}")
            for pol, r in res.items():
                y08 = "-" if r["y2008"] is None else f"{r['y2008']:+.1%}"
                print(
                    f"{pol:5} 끝 {r['final'] / 10_000:,.0f}만 (넣은 {r['paid'] / 10_000:,.0f}만)"
                    f" 연 {r['cagr_twr']:+.1%} 낙폭 {r['mdd']:.1%}({r['mdd_day']})"
                    f" 08 {y08} 20 {r['y2020']:+.1%} 22 {r['y2022']:+.1%}"
                    f" 주식 최고 {r['max_stock_weight']:.0%} 끝 {r['end_stock_weight']:.0%}"
                    f" 맞춤 {r['rebalances']}번 세금 {r['tax_paid'] / 10_000:,.0f}만"
                    f" 안 판 이익 {r['unrealized_end'] / 10_000:,.0f}만"
                )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result_rebal.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return 0


def split(px: Mapping[str, Series], days: list[str]) -> int:
    i0 = next(k for k, d in enumerate(days) if d >= "2005-01-01")
    i1 = max(k for k, d in enumerate(days) if d <= "2026-09-30")
    vals, _ = simulate(px, days, POLARIS, i0, i1, monthly=0.0)
    span = days[i0 : i1 + 1]
    firsts = [k for k in range(len(span)) if k == 0 or span[k][:7] != span[k - 1][:7]]
    plans = {"4x100": [1_000_000.0] * 4, "8x50": [500_000.0] * 8}
    rows: list[dict[str, Any]] = []
    for m in range(len(firsts) - 24):
        row: dict[str, Any] = {"start": span[firsts[m]][:7]}
        for name, deps in plans.items():
            units = paid = 0.0
            worst = 0.0
            v12 = v24 = 0.0
            for j in range(firsts[m], firsts[m + 24] + 1):
                if j in firsts[m : m + len(deps)]:
                    add = deps[firsts.index(j) - m]
                    units += add / vals[j]
                    paid += add
                value = units * vals[j] + (4_000_000.0 - paid)  # 안 넣은 돈은 현금
                worst = min(worst, value - 4_000_000.0)
                if j == firsts[m + 12]:
                    v12 = value
                if j == firsts[m + 24]:
                    v24 = value
            row[name] = {"v12": v12, "v24": v24, "worst": worst}
        rows.append(row)

    def pct(xs: list[float], q: float) -> float:
        ys = sorted(xs)
        return ys[int(q * (len(ys) - 1))]

    out: dict[str, Any] = {"starts": len(rows), "from": rows[0]["start"], "to": rows[-1]["start"]}
    for h in ("v12", "v24", "worst"):
        a = [r["4x100"][h] for r in rows]
        b = [r["8x50"][h] for r in rows]
        out[h] = {
            "4x100_mean": sum(a) / len(a),
            "8x50_mean": sum(b) / len(b),
            "4x100_p10": pct(a, 0.10),
            "8x50_p10": pct(b, 0.10),
            "4x100_min": min(a),
            "8x50_min": min(b),
            "4x100_better": sum(x > y for x, y in zip(a, b, strict=True)) / len(a),
        }
    worst_start = min(rows, key=lambda r: r["4x100"]["worst"])
    out["worst_start"] = worst_start
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result_split.json").write_text(
        json.dumps({"summary": out, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


def mix(sym: str) -> Rule:
    """주식 ``sym`` 60 · 국채 20 · 금 20."""
    return lambda i: {sym: 0.6, "IEF": 0.2, "GLD": 0.2}


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


def main(which: str = "base") -> int:
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
    out: dict[str, Any] = {"tqqq_synthetic_drag": drag}
    if which in ("lev", "lev6"):
        spy_r = rets(raw["SPY"], days)
        for sym, mult in (("SSO", 2.0), ("UPRO", 3.0)):
            real = load(DATA / "namu_core_compare" / f"{sym}.csv")
            d = fit_drag(spy_r, real, days, mult)
            px[sym] = chain(real, {k: mult * r - d / 252 for k, r in spy_r.items()}, days)
            out[f"{sym.lower()}_synthetic_drag"] = d
        real_qld = load(DATA / "lev_div_181" / "QLD.csv")
        d2 = fit_drag(qqq_r, real_qld, days, 2.0)
        px["QLD"] = chain(real_qld, {k: 2 * r - d2 / 252 for k, r in qqq_r.items()}, days)
        out["qld_synthetic_drag"] = d2
        rules = {
            "C1": lambda i: {"SPY": 0.6, "IEF": 0.2, "GLD": 0.2},
            "C2": lambda i: {"SSO": 0.6, "IEF": 0.2, "GLD": 0.2},
            "C3": lambda i: {"UPRO": 0.6, "IEF": 0.2, "GLD": 0.2},
            "Deneb": deneb,
        }
    if which == "lev6":
        pairs = [("S1", "SPY"), ("S2", "SSO"), ("S3", "UPRO")]
        pairs += [("N1", "QQQ"), ("N2", "QLD"), ("N3", "TQQQ")]
        rules = {tag: mix(sym) for tag, sym in pairs}
        rules["Deneb"] = deneb
    if which == "half":
        rules = {
            "S1": mix("SPY"),
            "H": lambda i: {"SPY": 0.3, "QQQ": 0.3, "IEF": 0.2, "GLD": 0.2},
            "N1": mix("QQQ"),
        }
    if which == "split":
        return split(px, days)
    if which == "rebal":
        return rebal(px, days)
    if which == "dca20":
        return dca20(px, days, {"Polaris": POLARIS, "Deneb": deneb})
    for wname, (a, b) in WINDOWS.items():
        i0 = next(k for k, d in enumerate(days) if d >= a)
        i1 = max(k for k, d in enumerate(days) if d <= b)
        res: dict[str, Any] = {"from": days[i0], "to": days[i1]}
        for name, rule in rules.items():
            vals, _ = simulate(px, days, rule, i0, i1, monthly=0.0)
            dv, dp = simulate(px, days, rule, i0, i1, monthly=1.0)
            res[name] = {"lump": stats(days[i0 : i1 + 1], vals), "dca": dca_stats(dv, dp)}
        out[wname] = res
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("result.json" if which == "base" else f"result_{which}.json")).write_text(
        json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print({k: f"{v:.2%}" for k, v in out.items() if k.endswith("_drag")})
    for wname in WINDOWS:
        res = out[wname]
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
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "base"))
