"""문서 185 — T 브레이크의 놓친 반등 줄이기, 쓰지 않은 12 개 시장으로 판정.

사용 예::

    python -m scripts.run_brake_185 run   # 1 회 판정 → reports/brake_185/result.json

* 사전 등록: docs/research/185-t-brake-missed-rebound-oos-preregistration.md.
* 가상 3 배 100% (한 번에 넣음), 판 돈 현금, 비용 편도 0.15%, 전날 종가 판단 → 그날 종가 체결.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_brake_183 import STAGE1, STAGE2, signals
from scripts.run_qs_dotcom import _series

REPO = Path(__file__).resolve().parent.parent
XMKT = REPO / "data" / "research" / "qs_bottom_xmkt"
SHILLER = REPO / "data" / "research" / "qs_xmkt" / "shiller_sp500.csv"
OUT = REPO / "reports" / "brake_185"
COST = 0.0015
FEE_3X = 0.0095
MARKETS = {
    "GSPC": ("1928-01-01", "1985-12-31"),
    **dict.fromkeys(
        ("AXJO", "BSESN", "BVSP", "FCHI", "FTSE", "GDAXI", "GSPTSE", "HSI", "KS11", "N225", "TWII"),
        ("1900-01-01", "2026-12-31"),
    ),
}
VARIANTS = {"T0": (False, False), "A": (True, False), "B": (False, True), "AB": (True, True)}
P_WIN, P_MDD = 8, 0.05
FLOOR = -0.99
"""하루 손실 하한 — 3 배 상품은 0 아래로 못 간다(HSI 1987-10-26 −33% 같은 날은 사실상 청산)."""


def lev3(
    days: Sequence[str], base: Mapping[str, float], rf: Mapping[str, float]
) -> dict[str, float]:
    """가상 3 배: 110 ``leveraged`` 와 같되 하루 수익을 −99% 에서 멈춘다 (순수)."""
    out = {days[0]: 100.0}
    for prev, cur in pairwise(days):
        r = base[cur] / base[prev] - 1
        carry = (2 * rf.get(prev, 0.0) / 100 + FEE_3X) / 252
        out[cur] = out[prev] * (1 + max(3 * r - carry, FLOOR))
    return out


def simulate(
    days: Sequence[str],
    lev: Mapping[str, float],
    trend: Mapping[str, bool | None],
    *,
    brake: bool = True,
    price_rebuy: bool = False,
    trend_sell: bool = False,
) -> dict[str, Any]:
    """가상 3 배 100% 계좌 (순수). 시작 = 추세 신호가 생기는 첫날."""
    i0 = next(i for i, d in enumerate(days) if trend.get(d) is not None) + 1
    q, cash = 1.0 / lev[days[i0 - 1]] * (1 - COST), 0.0
    stage, off_seen, peak = 0, False, lev[days[i0 - 1]]
    sale_usd = sale_qty = 0.0
    sales = rebuys = 0
    vals: list[float] = []
    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        if brake and q > 0 and stage < 2:
            dd = 1 - lev[prev] / peak
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = q, 2
            elif dd >= STAGE1 and stage == 0 and (not trend_sell or trend.get(prev) is False):
                sell, stage = q / 2, 1
            if sell > 0:
                if sale_qty == 0:
                    off_seen = False
                cash += sell * lev[d] * (1 - COST)
                q -= sell
                sale_usd += sell * lev[d]
                sale_qty += sell
                sales += 1
        if brake and stage > 0 and sale_qty > 0:
            t = trend.get(prev)
            if t is False:
                off_seen = True
            by_trend = bool(t and off_seen)
            by_price = price_rebuy and lev[prev] > sale_usd / sale_qty
            if by_trend or by_price:
                q += cash * (1 - COST) / lev[d]
                cash, stage, off_seen = 0.0, 0, False
                sale_usd = sale_qty = 0.0
                peak = lev[d]
                rebuys += 1
        vals.append(q * lev[d] + cash)
        peak = max(peak, lev[d])
    yrs = (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[i0])).days / 365.25
    top, mdd = vals[0], 0.0
    for v in vals:
        top = max(top, v)
        mdd = max(mdd, 1 - v / top)
    return {
        "cagr": round(vals[-1] ** (1 / yrs) - 1, 4),
        "mdd": round(mdd, 4),
        "sales": sales,
        "rebuys": rebuys,
        "period": [days[i0], days[-1]],
    }


def _index(name: str, a: str, b: str) -> dict[str, float]:
    with (XMKT / f"{name}.csv").open(encoding="utf-8") as f:
        rows = {r["date"]: float(r["close"]) for r in csv.DictReader(f) if r["close"]}
    return {d: v for d, v in rows.items() if a <= d <= b and v > 0}


def _rates() -> dict[str, float]:
    """날짜 → 연 금리(%): DTB3, 1954 전은 Shiller 장기 금리(월)."""
    out: dict[str, float] = {}
    with SHILLER.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["Long Interest Rate"]:
                out[r["Date"][:7]] = float(r["Long Interest Rate"])
    return out


def rf_daily(days: Sequence[str], long_m: Mapping[str, float]) -> dict[str, float]:
    raw = _series("DTB3_full.csv", "DTB3")
    out: dict[str, float] = {}
    lv = 0.0
    for d in days:
        if d < "1954-01-04":
            out[d] = long_m.get(d[:7], lv)
            lv = out[d]
            continue
        lv = raw.get(d, lv)
        out[d] = lv
    return out


def run_market(name: str, long_m: Mapping[str, float]) -> dict[str, Any]:
    a, b = MARKETS[name]
    idx = _index(name, a, b)
    days = sorted(idx)
    lev = lev3(days, idx, rf_daily(days, long_m))
    _, trend = signals(days, idx)
    out: dict[str, Any] = {"BH": simulate(days, lev, trend, brake=False)}
    for k, (pr, ts) in VARIANTS.items():
        out[k] = simulate(days, lev, trend, price_rebuy=pr, trend_sell=ts)
    return out


def verdict(res: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("A", "B", "AB"):
        dc = [m[k]["cagr"] - m["T0"]["cagr"] for m in res.values()]
        dm = [m[k]["mdd"] - m["T0"]["mdd"] for m in res.values()]
        wins = sum(x >= 0 for x in dc)
        ok = wins >= P_WIN and statistics.median(dc) > 0 and statistics.median(dm) <= P_MDD
        out[k] = {
            "wins": wins,
            "median_cagr_diff": round(statistics.median(dc), 4),
            "median_mdd_diff": round(statistics.median(dm), 4),
            "pass": ok,
        }
    passed = [k for k in ("A", "B", "AB") if out[k]["pass"]]
    out["choice"] = max(passed, key=lambda k: out[k]["median_cagr_diff"]) if passed else "T0"
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    long_m = _rates()
    res = {m: run_market(m, long_m) for m in MARKETS}
    full = {
        "markets": res,
        "verdict": verdict(res),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
    return full


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_brake_185 run")
    res = run()
    for m, r in res["markets"].items():
        cells = " ".join(
            f"{k} {r[k]['cagr'] * 100:5.1f}/{r[k]['mdd'] * 100:4.0f}/{r[k]['sales']:3d}"
            for k in ("BH", "T0", "A", "B", "AB")
        )
        sys.stdout.write(f"{m:6s} {r['T0']['period'][0][:4]}~{r['T0']['period'][1][:4]} {cells}\n")
    sys.stdout.write(json.dumps(res["verdict"], ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
