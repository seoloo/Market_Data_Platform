"""문서 218 — '파동에너지 이론' 5 장 공식 간단 확인. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_wave_218          # 결과 → reports/wave_218/result.json · 화면
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.research.wave_218 import (  # noqa: E402
    MonthSums,
    bootstrap_excess,
    find_bottoms,
    slow_k,
    sma,
    states,
)

STOCKS = REPO / "data" / "lt_paper" / "bars"
COINS = REPO / "data" / "research" / "binance_daily"
OUT = REPO / "reports" / "wave_218"
H, REACH, GAP = 20, 10, 10
SPLIT = "2017-01"


def load(path: Path) -> tuple[list[str], dict[str, list[float]]]:
    with path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("close")]
    dates = [r["date"][:10] for r in rows]
    cols = {k: [float(r[k]) for r in rows] for k in ("open", "high", "low", "close")}
    return dates, cols


def scan(path: Path, sums: MonthSums) -> None:
    dates, c = load(path)
    o, hi, lo, cl = c["open"], c["high"], c["low"], c["close"]
    n = len(cl)
    if n < 300:
        return
    st = states(cl)
    m10, m20 = sma(cl, 10), sma(cl, 20)

    def fwd(t: int, h: int) -> float | None:
        return float(cl[t + h] / o[t + 1] - 1) if t + h < n and o[t + 1] > 0 else None

    def reach(t: int, level: float) -> float | None:
        return float(max(hi[t + 1 : t + 1 + REACH]) >= level) if t + REACH < n else None

    def put(name: str, t: int, v: float | None) -> None:
        if v is None:
            return
        month = dates[t][:7]
        sums.add(name, month, v)
        sums.add(f"{name}@{'early' if month < SPLIT else 'late'}", month, v)

    # 기준선 — 그 상태의 모든 날
    for t in range(n):
        s = st[t]
        if s in ("A", "B"):
            put(f"{s}_base", t, fwd(t, H))
            put(f"{s}_base_f10", t, fwd(t, 10))
        elif s == "C":
            put("C_base", t, reach(t, m20[t]))
            put("C_base_r10", t, reach(t, m10[t]))
    last: dict[str, int] = {}
    for b in find_bottoms(slow_k(hi, lo, cl)):
        t = b.t
        if t >= n or not st[t]:
            continue
        s = st[t]
        fam = f"{s}_{'HL' if b.higher else 'LL'}" if s in ("A", "B") else "C_sig"
        if t - last.get(fam, -10_000) <= GAP:
            continue
        last[fam] = t
        if s in ("A", "B"):
            put(fam, t, fwd(t, H))
            put(f"{fam}_f10", t, fwd(t, 10))
        else:
            put("C_sig", t, reach(t, m20[t]))
            put("C_sig_r10", t, reach(t, m10[t]))
            put("C_triple" if b.triple else "C_double_only", t, reach(t, m20[t]))


def summarize(sums: MonthSums) -> dict[str, Any]:
    months = sums.months()
    early = [m for m in months if m < SPLIT]
    late = [m for m in months if m >= SPLIT]

    def ex(sig: str, base: str) -> dict[str, Any]:
        point, lo, hi = bootstrap_excess(sums, sig, base, months=months)
        e = bootstrap_excess(sums, f"{sig}@early", f"{base}@early", months=early)[0]
        la = bootstrap_excess(sums, f"{sig}@late", f"{base}@late", months=late)[0]
        return {
            "n_sig": sums.count(sig),
            "n_base": sums.count(base),
            "mean_sig": sums.mean(sig),
            "mean_base": sums.mean(base),
            "excess": point,
            "ci": [lo, hi],
            "early": e,
            "late": la,
        }

    return {
        "A": ex("A_LL", "A_base"),
        "A_HL(참고)": ex("A_HL", "A_base"),
        "B_LL": ex("B_LL", "B_base"),
        "B_HL": ex("B_HL", "B_base"),
        "B_diff": ex("B_HL", "B_LL"),
        "C": ex("C_sig", "C_base"),
        "C_r10(참고)": ex("C_sig_r10", "C_base_r10"),
        "C_triple_vs_double(참고)": ex("C_triple", "C_double_only"),
        "A_f10(참고)": ex("A_LL_f10", "A_base_f10"),
    }


def verdict(r: dict[str, Any]) -> dict[str, str]:
    def one(x: dict[str, Any], extra: bool = True) -> str:
        if not x["excess"] > 0:
            return "불리"
        if x["ci"][0] > 0 and x["early"] > 0 and x["late"] > 0 and extra:
            return "지지"
        return "불명"

    out = {
        "A": one(r["A"]),
        "B": one(r["B_diff"], r["B_LL"]["excess"] <= 0),
        "C": one(r["C"]),
    }
    for k, key in (("A", "A"), ("B", "B_diff")):
        if out[k] == "지지" and r[key]["excess"] < 0.005:
            out[k] = "지지(작음 — 20 일 +0.5%p 미만)"
    return out


def main() -> int:
    stocks, coins = MonthSums(), MonthSums()
    files = sorted(STOCKS.glob("*.csv"))
    for p in files:
        scan(p, stocks)
    for p in sorted(COINS.glob("*.csv")):
        scan(p, coins)
    summary = summarize(stocks)
    res: dict[str, Any] = {
        "stocks": summary,
        "coins(참고)": summarize(coins),
        "n_files": len(files),
        "verdict": verdict(summary),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
