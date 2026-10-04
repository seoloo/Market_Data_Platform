"""문서 119 — QS 닷컴 구간 확인 (1990-01 ~ 2000-12 시작 10 년 창).

사용 예::

    python -m scripts.run_qs_dotcom run     # 한 번 계산 → reports/qs_dotcom/result.json

자료: FRED ``NASDAQ100`` (지수 종가) · ``DTB3`` (3 개월 국채) — ``data/research/tradfi/fred/``,
SHA256 고정. 계산은 110 의 ``leveraged``·``trend_signals``·``simulate`` 그대로.
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_qs_switch import (
    QLD_FEE_YEAR,
    TQQQ_FEE_YEAR,
    irr,
    leveraged,
    simulate,
    trend_signals,
)

REPO = Path(__file__).resolve().parent.parent
FRED = REPO / "data" / "research" / "tradfi" / "fred"
OUT = REPO / "reports" / "qs_dotcom"
SHA = {
    "NASDAQ100.csv": "311fa922843ed508521899ac9776064f4e7f287eba5897269f2e8255f700faf8",
    "DTB3_full.csv": "ac8d8e667dfa992832049afa9393d6135976b1a32cef64979aff3d01f0e6ec82",
}
FIRST_START, LAST_START = "1990-01", "2000-12"
PEAK_START = "2000-03"
MONTHS = 120
KEEP, KEEP_PEAK, KEEP_DD = 0.70, 0.90, 0.15
CUT, CUT_PEAK, CUT_DD = 0.50, 0.80, 0.25


def _check(name: str) -> Path:
    path = FRED / name
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SHA[name]:
        raise SystemExit(f"{name} 해시가 고정값과 다르다 — 판정하지 않는다")
    return path


def _series(name: str, col: str) -> dict[str, float]:
    out = {}
    with _check(name).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            v = row[col].strip()
            if v and v != ".":
                out[row["observation_date"]] = float(v)
    return out


def _add_months(ym: str, n: int) -> str:
    y, m = (int(x) for x in ym.split("-"))
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate_raw = _series("DTB3_full.csv", "DTB3")
    days = sorted(ndx)
    rf: dict[str, float] = {}
    last = 0.0
    for d in sorted(set(days) | set(rate_raw)):
        last = rate_raw.get(d, last)
        rf[d] = last
    sig = trend_signals(days, ndx)
    tqqq = leveraged(days, ndx, rf, 3.0, TQQQ_FEE_YEAR)
    qld = leveraged(days, ndx, rf, 2.0, QLD_FEE_YEAR)
    months = sorted({d[:7] for d in days})
    starts = [m for m in months if FIRST_START <= m <= LAST_START]
    first_day = {m: min(d for d in days if d[:7] == m) for m in months}
    windows: list[dict[str, Any]] = []
    for m in starts:
        end_m = _add_months(m, MONTHS - 1)
        end_days = [d for d in days if d[:7] == end_m]
        if not end_days:
            continue
        s, e = first_day[m], end_days[-1]
        a = simulate(days, ndx, tqqq, sig, rf, mode="A", start=s, end=e)
        q = simulate(days, ndx, tqqq, sig, rf, mode="QS", start=s, end=e)
        q2 = simulate(days, ndx, qld, sig, rf, mode="QS", start=s, end=e)
        windows.append(
            {
                "start": s,
                "end": e,
                "a_final": round(a.final, 2),
                "qs_final": round(q.final, 2),
                "qs2_final": round(q2.final, 2),
                "ratio": round(q.final / a.final, 4),
                "ratio2": round(q2.final / a.final, 4),
                "a_dd": round(a.max_drawdown, 4),
                "qs_dd": round(q.max_drawdown, 4),
                "a_irr": irr(a.flows),
                "qs_irr": irr(q.flows),
                "qs_trades": q.trades,
                "qs_tax": round(q.tax_paid, 2),
            }
        )
    d1 = sum(w["ratio"] > 1 for w in windows) / len(windows)
    peak = next(w for w in windows if w["start"][:7] == PEAK_START)
    d2 = peak["ratio"]
    d3 = max(w["qs_dd"] - w["a_dd"] for w in windows)
    if d1 >= KEEP and d2 >= KEEP_PEAK and d3 <= KEEP_DD:
        verdict = "KEEP"
    elif d1 < CUT or d2 < CUT_PEAK or d3 > CUT_DD:
        verdict = "CUT_SUGGEST"
    else:
        verdict = "KEEP_WARN"
    # 보고만: 2000~2002 TQQQ 매매 (꼭대기 창), 2 배 변형
    crash = simulate(
        days, ndx, tqqq, sig, rf, mode="QS", start=first_day["2000-01"], end="2002-12-31"
    )
    ratios = [w["ratio"] for w in windows]
    result = {
        "verdict": verdict,
        "D1_share_qs_wins": round(d1, 4),
        "D2_peak_ratio": d2,
        "D3_worst_dd_gap": round(d3, 4),
        "windows": len(windows),
        "ratio_min_median_max": [min(ratios), round(statistics.median(ratios), 4), max(ratios)],
        "qs2_share_wins": round(sum(w["ratio2"] > 1 for w in windows) / len(windows), 4),
        "qs2_ratio_min_median_max": [
            min(w["ratio2"] for w in windows),
            round(statistics.median(w["ratio2"] for w in windows), 4),
            max(w["ratio2"] for w in windows),
        ],
        "peak_window": peak,
        "crash_2000_2002": {
            "trades": crash.trades,
            "round_trips": crash.round_trips,
            "final": round(crash.final, 2),
            "invested": crash.invested,
            "max_cash": round(crash.max_cash, 2),
        },
        "worst_ratio_windows": sorted(windows, key=lambda w: w["ratio"])[:5],
        "all_windows": windows,
        "data_range": [days[0], days[-1]],
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: v for k, v in result.items() if k not in ("all_windows",)}


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_qs_dotcom run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
