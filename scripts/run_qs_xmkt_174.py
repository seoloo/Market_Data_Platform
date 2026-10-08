"""문서 174 — QS 를 나스닥이 아닌 지수 12 개에 그대로 (1928 ~ 2026, 주문 없음).

사용::

    python scripts/run_qs_xmkt_174.py run   # 한 번만 (reports/qs_xmkt_174/verdict.json 있으면 거부)

* 규칙 · 계산은 문서 110 그대로: ``scripts/run_qs_switch`` 의
  ``leveraged`` · ``trend_signals`` · ``simulate``
  (매달 첫 거래일 1,000, 수수료 0.25%, QS 실현 이익 해마다 22%, 현금 이자 없음, 가상 3 배 =
  3 × 기초 수익 − (2 × 전날 금리 + 0.84%) / 252, 전날 신호를 오늘 종가에 실행).
* 자료: 문서 168 의 Yahoo 지수 일봉 12 개(가격 지수). S&P 500 만 Shiller 월 배당으로 총수익을 만든다
  (일간 수익 + 그 달 배당 수익률 / 252, 2023-07 이후는 마지막 값 1.58% 를 이어 씀).
* 금리: FRED DTB3 (1954-01-04 ~), 그 전은 3%. 모든 시장에 미국 금리를 쓴다(약점).
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from scripts.run_qs_switch import (  # noqa: E402
    QLD_FEE_YEAR,
    TQQQ_FEE_YEAR,
    leveraged,
    simulate,
    trend_signals,
)

MARKETS = {
    "GSPC": "S&P 500 (총수익)",
    "N225": "닛케이 225",
    "FTSE": "FTSE 100",
    "GSPTSE": "캐나다 TSX",
    "GDAXI": "독일 DAX",
    "HSI": "홍콩 항셍",
    "FCHI": "프랑스 CAC 40",
    "AXJO": "호주 ASX 200",
    "BVSP": "브라질 보베스파",
    "KS11": "한국 KOSPI",
    "TWII": "대만 가권",
    "BSESN": "인도 센섹스",
}
IDX = REPO / "data" / "research" / "qs_bottom_xmkt"
SHILLER = REPO / "data" / "research" / "qs_xmkt" / "shiller_sp500.csv"
SHILLER_SHA = "3a45dffabc414afc298c12159abc84c2875f88175ff3d4473ef7417e725ecac6"
DTB3 = REPO / "data" / "research" / "tradfi" / "fred" / "DTB3_full.csv"
OUT = REPO / "reports" / "qs_xmkt_174"
END = "2026-08-31"
LAST_START = "2016-08-31"
WARMUP = 126
RATE_BEFORE_1954 = 3.0
WIN_SHARE, MEDIAN_MIN, BAD_MEDIAN = 0.60, 1.00, 0.90
KEEP_MARKETS, KEEP_MAX_BAD = 8, 2
CUT_MAX_GOOD, CUT_MIN_BAD = 4, 4


def load_index(sym: str) -> dict[str, float]:
    with (IDX / f"{sym}.csv").open(encoding="utf-8") as f:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(f) if r["date"] <= END}


def shiller_yield() -> dict[str, float]:
    """{'YYYY-MM': 연 배당 수익률}. 배당이 0 인 끝 달은 마지막 값을 이어 쓴다."""
    raw = SHILLER.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHILLER_SHA:
        raise SystemExit("Shiller 자료 해시가 다르다")
    out: dict[str, float] = {}
    last = 0.0
    for r in csv.DictReader(raw.decode("utf-8").splitlines()):
        d, p = float(r["Dividend"]), float(r["SP500"])
        if d > 0 and p > 0:
            last = d / p
        out[r["Date"][:7]] = last
    return out


def total_return(days: list[str], close: dict[str, float]) -> dict[str, float]:
    y = shiller_yield()
    out = {days[0]: close[days[0]]}
    for prev, cur in pairwise(days):
        r = close[cur] / close[prev] - 1 + y.get(cur[:7], 0.0) / 252
        out[cur] = out[prev] * (1 + r)
    return out


def rates(days: list[str]) -> dict[str, float]:
    with DTB3.open(encoding="utf-8") as f:
        raw = {
            r["observation_date"]: float(r["DTB3"])
            for r in csv.DictReader(f)
            if r["DTB3"] not in ("", ".")
        }
    out: dict[str, float] = {}
    last = RATE_BEFORE_1954
    for d in days:
        last = raw.get(d, last if d >= "1954-01-04" else RATE_BEFORE_1954)
        out[d] = last
    return out


def windows(days: list[str]) -> list[tuple[str, str]]:
    first = days[WARMUP]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in days:
        if d < first or d > LAST_START or d[:7] in seen:
            continue
        seen.add(d[:7])
        y, m, dd = (int(x) for x in d.split("-"))
        end = date(y + 10, m, min(dd, 28)).isoformat()
        out.append((d, max(x for x in days if x <= end)))
    return out


def market(sym: str) -> dict[str, Any]:
    close = load_index(sym)
    days = sorted(close)
    base = total_return(days, close) if sym == "GSPC" else close
    rf = rates(days)
    tq = leveraged(days, base, rf, 3.0, TQQQ_FEE_YEAR)
    q2 = leveraged(days, base, rf, 2.0, QLD_FEE_YEAR)
    sig = trend_signals(days, base)
    ratios, ratios2, mdd_diff, starts = [], [], [], []
    for ws, we in windows(days):
        a = simulate(days, base, tq, sig, rf, mode="A", start=ws, end=we)
        q = simulate(days, base, tq, sig, rf, mode="QS", start=ws, end=we)
        q_2x = simulate(days, base, q2, sig, rf, mode="QS", start=ws, end=we)
        ratios.append(q.final / a.final)
        ratios2.append(q_2x.final / a.final)
        mdd_diff.append(q.max_drawdown - a.max_drawdown)
        starts.append(ws)
    n = len(ratios)
    by_decade: dict[str, list[float]] = {}
    for s, r in zip(starts, ratios, strict=True):
        by_decade.setdefault(s[:3] + "0s", []).append(r)
    win = sum(1 for r in ratios if r > 1) / n
    med = statistics.median(ratios)
    return {
        "name": MARKETS[sym],
        "first": days[0],
        "windows": n,
        "win_share": round(win, 4),
        "median_ratio": round(med, 4),
        "min_ratio": round(min(ratios), 4),
        "max_ratio": round(max(ratios), 4),
        "mdd_diff_median": round(statistics.median(mdd_diff), 4),
        "mdd_diff_max": round(max(mdd_diff), 4),
        "good": win >= WIN_SHARE and med >= MEDIAN_MIN,
        "bad": med < BAD_MEDIAN,
        "ref_2x_median_ratio": round(statistics.median(ratios2), 4),
        "ref_2x_win_share": round(sum(1 for r in ratios2 if r > 1) / n, 4),
        "by_decade_median": {
            k: round(statistics.median(v), 4) for k, v in sorted(by_decade.items())
        },
    }


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    res = {sym: market(sym) for sym in MARKETS}
    good = sum(1 for v in res.values() if v["good"])
    bad = sum(1 for v in res.values() if v["bad"])
    if good >= KEEP_MARKETS and bad <= KEEP_MAX_BAD:
        verdict = "유지 (일반화 근거)"
    elif good <= CUT_MAX_GOOD or bad >= CUT_MIN_BAD:
        verdict = "축소 제안"
    else:
        verdict = "유지 + 경고"
    out = {"markets": res, "good": good, "bad": bad, "verdict": verdict}
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit(__doc__)
    out = run()
    for v in out["markets"].values():
        print(
            f"{v['name']:<16} 창 {v['windows']:>4} 승 {v['win_share']:.0%}"
            f" 중앙 {v['median_ratio']:.2f}"
            f" (최소 {v['min_ratio']:.2f}) 낙폭차 중앙 {v['mdd_diff_median']:+.1%}"
            f" · 2배 중앙 {v['ref_2x_median_ratio']:.2f} {'좋음' if v['good'] else ''}"
            f"{'나쁨' if v['bad'] else ''}"
        )
    print(f"좋음 {out['good']} · 나쁨 {out['bad']} → {out['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
