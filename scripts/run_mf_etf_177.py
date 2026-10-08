"""문서 177 — 추세 추종 소매(176 B)를 실제로 살 수 있는 ETF 로: 기록형 측정 (판정 없음, 주문 없음).

사용::

    python -m scripts.run_mf_etf_177 fetch   # Yahoo 일봉(수정 종가) — 로그인 없음
    python -m scripts.run_mf_etf_177 run     # reports/mf_etf_177/result.json

* ETF: DBMF(2019-05) · KMLM(2020-12) · CTA(2022-03). 월말 수정 종가(분배 재투자) 수익.
* 비교: 176 B 의 소매 = AQR TSMOM 초과수익 + 현금(French RF) − 연 1% / 12.
* 잰다: ETF 마다 겹치는 달의 상관 · 연 수익 차(ETF − 지수) · 달력 연도 수익 · 최대 낙폭.
  176 B 를 같은 기간 ETF 고른 비중 소매로 바꿔 나스닥 100 만 대비 샤프 · 낙폭을 적는다(판정 아님).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from scripts.run_multi_asset_176 import COST, build_table  # noqa: E402

from auto_trading.research.multi_asset import turnover  # noqa: E402

ETFS = ("DBMF", "KMLM", "CTA")
DATA = REPO / "data" / "research" / "mf_etf_177"
OUT = REPO / "reports" / "mf_etf_177"
END = "2026-08"
SLEEVE_FEE = 0.01
MIX = 0.2


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    for sym in ETFS:
        r = httpx.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"period1": 0, "period2": 1_900_000_000, "interval": "1d", "events": "div"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=60,
        )
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        adj = res["indicators"]["adjclose"][0]["adjclose"]
        with (DATA / f"{sym}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "adjclose"])
            for t, a in zip(res["timestamp"], adj, strict=True):
                if a:
                    w.writerow([datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d"), a])
        print(sym, len(res["timestamp"]))


def monthly(sym: str) -> dict[str, float]:
    with (DATA / f"{sym}.csv").open(encoding="utf-8") as f:
        level: dict[str, float] = {}
        for r in csv.DictReader(f):
            level[r["date"][:7]] = float(r["adjclose"])  # 그 달 마지막 거래일
    ms = [m for m in sorted(level) if m <= END]
    first = ms[0]  # 첫 달은 상장 중간이라 수익에서 뺀다
    return {b: level[b] / level[a] - 1 for a, b in pairwise(ms) if a >= first}


def _stats(rs: list[float]) -> dict[str, float]:
    n = len(rs)
    mean, sd = statistics.mean(rs), statistics.pstdev(rs)
    nav, peak, mdd = 1.0, 1.0, 0.0
    for r in rs:
        nav *= 1 + r
        peak = max(peak, nav)
        mdd = max(mdd, 1 - nav / peak)
    return {
        "months": n,
        "cagr": round(nav ** (12 / n) - 1, 4),
        "vol": round(sd * 12**0.5, 4),
        "sharpe0": round(mean / sd * 12**0.5, 3) if sd else 0.0,
        "mdd": round(mdd, 4),
    }


def _years(ms: list[str], rs: list[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for m, r in zip(ms, rs, strict=True):
        out[m[:4]] = out.get(m[:4], 1.0) * (1 + r)
    return {y: round(v - 1, 4) for y, v in out.items()}


def mix(eq: list[float], sl: list[float]) -> list[float]:
    """매달 말 20% 로 되돌림 · 회전 × 편도 비용 — 176 ``run_b`` 와 같은 계산."""
    out = []
    w_old = {"E": 1 - MIX, "S": MIX}
    target = {"E": 1 - MIX, "S": MIX}
    for e, s in zip(eq, sl, strict=True):
        out.append((1 - MIX) * e + MIX * s - COST * turnover(w_old, target))
        ge, gs = (1 - MIX) * (1 + e), MIX * (1 + s)
        w_old = {"E": ge / (ge + gs), "S": gs / (ge + gs)}
    return out


def run() -> dict[str, Any]:
    table = build_table()
    idx = {
        m: table[m]["TSMOM_EX"] + table[m]["RF"] - SLEEVE_FEE / 12
        for m in table
        if "TSMOM_EX" in table[m] and "RF" in table[m]
    }
    ndx = {m: table[m]["NDX"] for m in table if "NDX" in table[m]}
    etf = {s: monthly(s) for s in ETFS}
    out: dict[str, Any] = {"per_etf": {}, "note": "기록형 — 판정 없음 (문서 177)"}
    for s, rets in etf.items():
        ms = sorted(m for m in rets if m in idx)
        a, b = [rets[m] for m in ms], [idx[m] for m in ms]
        out["per_etf"][s] = {
            "overlap": [ms[0], ms[-1]],
            "corr_with_index": round(statistics.correlation(a, b), 3),
            "etf": _stats(a),
            "index": _stats(b),
            "years_etf": _years(ms, a),
            "years_index": _years(ms, b),
            "all_months_etf": _stats([rets[m] for m in sorted(rets)]),
        }
    # 176 B 를 ETF 소매로: 그 달 있는 ETF 들의 고른 평균
    ms = sorted(m for m in ndx if any(m in etf[s] for s in ETFS))
    sleeve = [statistics.mean(etf[s][m] for s in ETFS if m in etf[s]) for m in ms]
    eq = [ndx[m] for m in ms]
    mixed = mix(eq, sleeve)
    ms_i = [m for m in ms if m in idx]
    out["b_with_etfs"] = {
        "period": [ms[0], ms[-1]],
        "ndx_only": _stats(eq),
        "sleeve_only": _stats(sleeve),
        "mix20": _stats(mixed),
        "years_mix20": _years(ms, mixed),
        "years_ndx": _years(ms, eq),
        "same_period_index_sleeve_mix20": _stats(
            mix([ndx[m] for m in ms_i], [idx[m] for m in ms_i])
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


def main(argv: list[str]) -> int:
    if argv == ["fetch"]:
        fetch()
        return 0
    if argv == ["run"]:
        print(json.dumps(run(), ensure_ascii=False, indent=1))
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
