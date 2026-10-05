"""문서 153 — 배당·커버드콜 ETF(SCHD · JEPQ · JEPI · QYLD 등) 와 SPY · QQQ 비교. 참고 계산.

사용 예::

    python -m scripts.ref_dividend_etf   # data/research/tradfi/div/*.csv (Tiingo) → 표 출력

* 총수익 = 배당 재투자(Tiingo 수정 종가). 가격만 = 배당을 뺀 수익.
* 세후(한국 거주자 가정): 배당은 받을 때마다 미국 원천징수 15% 뒤 재투자, 끝에 팔 때 양도차익 22%
  (기본공제 250 만 원·금융소득 종합과세는 넣지 않음).
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIV = REPO / "data" / "research" / "tradfi" / "div"
END = "2026-09-30"
DIV_TAX, GAIN_TAX = 0.15, 0.22


@dataclass(frozen=True, slots=True)
class Series:
    dates: list[str]
    close: list[float]
    adj: list[float]
    div: list[float]


def load(sym: str) -> Series:
    with (DIV / f"{sym}.csv").open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["date"] <= END]
    return Series(
        [r["date"] for r in rows],
        [float(r["close"]) for r in rows],
        [float(r["adj_close"]) for r in rows],
        [float(r["div"]) for r in rows],
    )


def factors(s: Series, i: int) -> tuple[float, float]:
    """(총수익 배수, 그날 배당 ÷ 전날 종가)."""
    return s.adj[i] / s.adj[i - 1], s.div[i] / s.close[i - 1]


def paths(s: Series, start: str) -> dict[str, list[float]]:
    """총수익 · 가격만 · 세후(배당 15% 떼고 재투자) 평가액과 세후 원가."""
    i0 = next(i for i, d in enumerate(s.dates) if d >= start)
    tot, price, net, basis = [1.0], [1.0], [1.0], [1.0]
    for i in range(i0 + 1, len(s.dates)):
        g, y = factors(s, i)
        tot.append(tot[-1] * g)
        price.append(price[-1] * (g - y))
        reinvest = net[-1] * y * (1 - DIV_TAX)
        net.append(net[-1] * (g - y) + reinvest)
        basis.append(basis[-1] + reinvest)
    out: dict[str, list[float]] = {"total": tot, "price": price, "net": net, "basis": basis}
    out["dates"] = s.dates[i0:]  # type: ignore[assignment]
    return out


def cagr(eq: Sequence[float], days: int) -> float:
    return (eq[-1] / eq[0]) ** (252 / days) - 1 if days > 0 else 0.0


def mdd(eq: Sequence[float]) -> float:
    peak, out = 0.0, 0.0
    for v in eq:
        peak = max(peak, v)
        out = max(out, 1 - v / peak)
    return out


def sharpe(eq: Sequence[float]) -> float:
    r = [b / a - 1 for a, b in pairwise(eq)]
    sd = statistics.stdev(r)
    return statistics.fmean(r) / sd * math.sqrt(252) if sd > 0 else 0.0


def after_tax_cagr(net: Sequence[float], basis: Sequence[float], days: int) -> float:
    """끝에 모두 팔고 양도차익 22% 를 낸 뒤의 연 수익."""
    final = net[-1] - max(net[-1] - basis[-1], 0.0) * GAIN_TAX
    return (final / net[0]) ** (252 / days) - 1


def yearly(dates: Sequence[str], eq: Sequence[float]) -> dict[str, float]:
    last: dict[str, int] = {}
    for i, d in enumerate(dates):
        last[d[:4]] = i
    out: dict[str, float] = {}
    prev = 0
    for y in sorted(last):
        out[y] = round(eq[last[y]] / eq[prev] - 1, 4)
        prev = last[y]
    return out


def ttm_yield(s: Series) -> float:
    """끝에서 1 년 배당 합 ÷ 끝 종가."""
    end = s.dates[-1]
    lo = f"{int(end[:4]) - 1}{end[4:]}"
    return sum(d for t, d in zip(s.dates, s.div, strict=True) if t > lo) / s.close[-1]


def capture(a: dict[str, list[float]], b: dict[str, list[float]]) -> dict[str, float]:
    """월 단위: 기준(b) 이 오른 달·내린 달에 a 가 따라간 비율 (평균 수익의 비)."""

    def monthly(p: dict[str, list[float]]) -> dict[str, float]:
        last: dict[str, float] = {}
        for d, v in zip(p["dates"], p["total"], strict=True):
            last[d[:7]] = v
        ks = sorted(last)
        return {k: last[k] / last[j] - 1 for j, k in pairwise(ks)}

    ma, mb = monthly(a), monthly(b)
    up = [(ma[k], mb[k]) for k in mb if k in ma and mb[k] > 0]
    dn = [(ma[k], mb[k]) for k in mb if k in ma and mb[k] < 0]
    return {
        "up": round(statistics.fmean(x for x, _ in up) / statistics.fmean(y for _, y in up), 2),
        "down": round(statistics.fmean(x for x, _ in dn) / statistics.fmean(y for _, y in dn), 2),
        "months": len(up) + len(dn),
    }


def compare(syms: Sequence[str], start: str, base: str) -> dict[str, object]:
    data = {s: load(s) for s in syms}
    start = max(start, *(d.dates[0] for d in data.values()))
    out: dict[str, object] = {"start": start, "end": END}
    ps = {s: paths(data[s], start) for s in syms}
    for s in syms:
        p = ps[s]
        n = len(p["total"]) - 1
        out[s] = {
            "cagr_total": round(cagr(p["total"], n), 4),
            "cagr_price": round(cagr(p["price"], n), 4),
            "cagr_after_tax": round(after_tax_cagr(p["net"], p["basis"], n), 4),
            "mdd": round(mdd(p["total"]), 4),
            "sharpe": round(sharpe(p["total"]), 3),
            "yield_ttm": round(ttm_yield(data[s]), 4),
            "worst_year": min(yearly(p["dates"], p["total"]).items(), key=lambda kv: kv[1]),  # type: ignore[arg-type]
            "y2022": yearly(p["dates"], p["total"]).get("2022"),
            "capture_vs_" + base: capture(p, ps[base]) if s != base else None,
        }
    return out


def main(argv: list[str]) -> int:
    result = {
        "schd_family": compare(("SCHD", "VYM", "SPY", "QQQ"), "2011-10-20", "SPY"),
        "dgro": compare(("DGRO", "SCHD", "SPY", "QQQ"), "2014-06-12", "SPY"),
        "jepq": compare(("JEPQ", "QYLD", "QQQ", "SCHD"), "2022-05-04", "QQQ"),
        "jepi": compare(("JEPI", "SPY", "SCHD"), "2020-05-21", "SPY"),
        "qyld_long": compare(("QYLD", "QQQ"), "2013-12-12", "QQQ"),
    }
    by_year = defaultdict(dict)
    for s in ("SCHD", "SPY", "QQQ", "JEPQ", "JEPI", "QYLD"):
        p = paths(load(s), "2011-10-20")
        for y, v in yearly(p["dates"], p["total"]).items():
            by_year[y][s] = v
    result["by_year_total"] = dict(sorted(by_year.items()))
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if argv and argv[0] == "--out":
        Path(argv[1]).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
