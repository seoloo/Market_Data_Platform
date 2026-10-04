"""문서 128 — 듀얼 모멘텀 (Antonacci GEM): 책 밖 2014-01 ~ 2026-08 검증.

사용 예::

    python -m scripts.run_gem fetch   # EFA·AGG·BIL 일봉 받기 (한국투자 읽기 전용)
    python -m scripts.run_gem run     # 판정 한 번 → reports/gem/result.json
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_index_mr import trend_returns
from scripts.run_qs_switch import read_close

REPO = Path(__file__).resolve().parent.parent
GEM = REPO / "data" / "research" / "tradfi" / "gem"
OUT = REPO / "reports" / "gem"
SYMBOLS = {"EFA": "AMS", "AGG": "AMS", "BIL": "AMS"}
SHA: dict[str, str] = {
    "EFA": "255c610f07ca15385552e2c944cae3e28158234cdbe93388e5945101a1ffbfe7",
    "AGG": "09ed3f9811a2f4ce281b2a9fb666251d21c05b797b451a1e6a87511beba10155",
    "BIL": "6606e529078654a3b57aa116526e7b609f2dc966e985b372bf0e22b6eb131fbd",
}
"""fetch 뒤(수익 보기 전) 고정한다."""
COST = 0.0012
LOOK = 252
OOS = ("2014-01-01", "2026-08-31")
IN_BOOK = ("2008-08-01", "2013-12-31")
P1_GAP, P2_GAP = 0.02, 0.10


def fetch() -> dict[str, object]:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    broker = KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )
    GEM.mkdir(parents=True, exist_ok=True)
    out: dict[str, object] = {}
    try:
        for sym, excd in SYMBOLS.items():
            bars = broker.daily_history(sym, since="2007-01-01", exchange=excd, adjusted=True)
            path = GEM / f"{sym}.csv"
            with path.open("w", encoding="utf-8", newline="\n") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(["date", "close"])
                for b in bars:
                    d = datetime.fromtimestamp(b.open_ms / 1000, UTC).strftime("%Y-%m-%d")
                    w.writerow([d, str(b.close)])
            out[sym] = {
                "rows": len(bars),
                "first": datetime.fromtimestamp(bars[0].open_ms / 1000, UTC).date().isoformat(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    finally:
        broker.close()
    return out


def _load(sym: str) -> dict[str, float]:
    path = GEM / f"{sym}.csv"
    if hashlib.sha256(path.read_bytes()).hexdigest() != SHA.get(sym):
        raise SystemExit(f"{sym} 해시가 고정값과 다르다 — 판정하지 않는다")
    with path.open(encoding="utf-8") as fh:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(fh)}


def gem_returns(
    days: Sequence[str], px: Mapping[str, Mapping[str, float]]
) -> tuple[list[float], list[str]]:
    """(일간 수익, 날마다 든 자산). 월말 신호 → 다음 거래일 종가."""
    hold = "BIL"
    pending: str | None = None
    rets = [0.0]
    held = [hold]
    for t in range(1, len(days)):
        d, prev = days[t], days[t - 1]
        r = px[hold][d] / px[hold][prev] - 1
        if pending is not None and pending != hold:
            r -= COST * (2 if hold != "BIL" else 1)
            hold = pending
        pending = None
        rets.append(r)
        held.append(hold)
        month_end = t + 1 < len(days) and days[t + 1][:7] != d[:7]
        if month_end and t >= LOOK:
            m = {s: px[s][d] / px[s][days[t - LOOK]] - 1 for s in ("SPY", "EFA", "BIL")}
            pending = ("SPY" if m["SPY"] >= m["EFA"] else "EFA") if m["SPY"] > m["BIL"] else "AGG"
    return rets, held


def stats(rets: Sequence[float]) -> dict[str, float]:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    years = len(rets) / 252
    sd = statistics.stdev(rets)
    return {
        "cagr": round(eq ** (1 / years) - 1, 4),
        "sharpe": round(statistics.fmean(rets) / sd * math.sqrt(252), 3) if sd > 0 else 0.0,
        "mdd": round(mdd, 4),
    }


def _year(seg: Any, rets: Sequence[float], spy: Sequence[float], y: str) -> dict[str, Any]:
    lo, hi = f"{y}-01-01", f"{y}-12-31"
    return {"GEM": stats(seg(rets, lo, hi)), "SPY": stats(seg(spy, lo, hi))}


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    px: dict[str, dict[str, float]] = {s: _load(s) for s in SYMBOLS}
    px["SPY"] = read_close("SPY_adj")
    qqq = read_close("QQQ_adj")
    days = sorted(set.intersection(*(set(v) for v in px.values())) & set(qqq))
    days = [d for d in days if d <= OOS[1]]
    rets, held = gem_returns(days, px)
    spy = [0.0] + [px["SPY"][b] / px["SPY"][a] - 1 for a, b in itertools.pairwise(days)]
    qtr = trend_returns(days, [qqq[d] for d in days])
    sixty = []
    vs, va = 0.6, 0.4
    month = ""
    for i, d in enumerate(days):
        if d[:7] != month:
            month = d[:7]
            tot = vs + va
            vs, va = 0.6 * tot, 0.4 * tot
        if i == 0:
            sixty.append(0.0)
            continue
        before = vs + va
        vs *= px["SPY"][d] / px["SPY"][days[i - 1]]
        va *= px["AGG"][d] / px["AGG"][days[i - 1]]
        sixty.append((vs + va) / before - 1)

    def seg(xs: Sequence[float], lo: str, hi: str) -> list[float]:
        return [x for d, x in zip(days, xs, strict=True) if lo <= d <= hi]

    g, s = stats(seg(rets, *OOS)), stats(seg(spy, *OOS))
    p1 = g["cagr"] >= s["cagr"] - P1_GAP
    p2 = g["mdd"] <= s["mdd"] - P2_GAP
    p3 = g["sharpe"] >= s["sharpe"]
    oos_held = seg(held, *OOS)  # type: ignore[arg-type]
    switches = sum(1 for a, b in itertools.pairwise(oos_held) if a != b)
    result = {
        "verdict": "PASS" if p1 and p2 and p3 else "REJECT",
        "P1": p1,
        "P2": p2,
        "P3": p3,
        "oos": {
            "GEM": g,
            "SPY": s,
            "QQQ_trend": stats(seg(qtr, *OOS)),
            "60_40": stats(seg(sixty, *OOS)),
        },
        "in_book_report": {
            "GEM": stats(seg(rets, *IN_BOOK)),
            "SPY": stats(seg(spy, *IN_BOOK)),
        },
        "oos_switches": switches,
        "oos_share": {
            a: round(oos_held.count(a) / len(oos_held), 3) for a in ("SPY", "EFA", "AGG", "BIL")
        },
        "y2020": {
            "GEM": stats(seg(rets, "2020-01-01", "2020-12-31")),
            "SPY": stats(seg(spy, "2020-01-01", "2020-12-31")),
        },
        "y2022": {
            "GEM": stats(seg(rets, "2022-01-01", "2022-12-31")),
            "SPY": stats(seg(spy, "2022-01-01", "2022-12-31")),
        },
        "range": [days[0], days[-1]],
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv == ["fetch"]:
        result: dict[str, Any] = fetch()
    elif argv == ["run"]:
        if not SHA:
            raise SystemExit("자료 해시가 고정되지 않았다 — fetch 뒤 SHA 를 적고 커밋")
        result = run()
    else:
        raise SystemExit("usage: python -m scripts.run_gem fetch | run")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
