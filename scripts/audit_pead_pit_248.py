"""문서 248 — 240 실적 갭의 생존 편향 확인: S&P 500 에서 빠진 종목 자료 감사 (1 단계, 수익 안 봄).

사용 예::

    python -m scripts.audit_pead_pit_248

240 은 지금 S&P 500 명단(살아남은 종목)만 썼다. 생존 편향을 보려면 2008-06 ~ 2026-08 에
**명단에서 빠진 종목**의 실적 갭도 같은 규칙으로 봐야 한다. 그 전에 자료가 되는지만 센다.

* 빠진 종목: 208 위키 명단 변경(`events.json`)의 removed, 효력일 2008-06-01 ~ 2026-08-31.
* 가격: 손 안의 일봉(`data/lt_paper/bars` · `stocks` · `stocks_sp400` · 208 Tiingo `prices`) —
  빠지기 전 3 년(2008 이후) 중 가격 있는 거래일 비율.
* 실적 발표 시각: SEC `company_tickers.json`(지금 티커 → CIK, 1 회 요청)으로 CIK 를 찾을 수 있나.
* 관문: 빠진 종목 중 (가격 ≥ 90% 그리고 CIK 있음) ≥ 80% → `DATA_OK`(2 단계 사전 등록),
  아니면 `DATA_INSUFFICIENT`.
"""

from __future__ import annotations

import bisect
import csv
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
EVENTS = REPO / "data" / "research" / "sp500_events_208" / "events.json"
SOURCES = (
    REPO / "data" / "lt_paper" / "bars",
    REPO / "data" / "research" / "stocks",
    REPO / "data" / "research" / "stocks_sp400",
    REPO / "data" / "research" / "sp500_events_208" / "prices",
)
SPY = REPO / "data" / "research" / "tradfi" / "SPY_adj.csv"
OUT = REPO / "reports" / "pead_pit_248"
FROM, TO = "2008-06-01", "2026-08-31"
LOOKBACK_DAYS = 3 * 365
PRICE_MIN = 0.90
GATE = 0.80


def removed(events: list[dict[str, Any]]) -> dict[str, str]:
    """빠진 티커 → 마지막으로 빠진 효력일 (순수)."""
    out: dict[str, str] = {}
    for e in events:
        t, d = e.get("removed"), str(e.get("effective") or "")
        if t and FROM <= d <= TO:
            out[str(t).upper()] = max(out.get(str(t).upper(), ""), d)
    return out


def price_days(sym: str) -> set[str]:
    days: set[str] = set()
    for src in SOURCES:
        for name in (sym, sym.replace(".", "-"), sym.replace("-", ".")):
            p = src / f"{name}.csv"
            if p.exists():
                with p.open(encoding="utf-8") as f:
                    days |= {r["date"][:10] for r in csv.DictReader(f) if r.get("close")}
    return days


def coverage(sym_days: set[str], cal: list[str], end: str) -> float:
    start = max(FROM, (date.fromisoformat(end) - timedelta(days=LOOKBACK_DAYS)).isoformat())
    lo, hi = bisect.bisect_left(cal, start), bisect.bisect_right(cal, end)
    window = cal[lo:hi]
    return sum(1 for d in window if d in sym_days) / len(window) if window else 0.0


def main() -> int:
    import httpx
    from scripts.run_pead_240 import _get, _hdr

    gone = removed(json.loads(EVENTS.read_text(encoding="utf-8")))
    with SPY.open(encoding="utf-8") as f:
        cal = sorted(r["date"][:10] for r in csv.DictReader(f))
    with httpx.Client(timeout=30.0, headers=_hdr()) as c:
        m = _get(c, "https://www.sec.gov/files/company_tickers.json")
    cik = {v["ticker"].upper(): int(v["cik_str"]) for v in m.values()}
    rows = []
    for t, d in sorted(gone.items()):
        cov = coverage(price_days(t), cal, d)
        has_cik = t.replace(".", "-") in cik or t in cik
        rows.append({"ticker": t, "removed": d, "price_cov": round(cov, 3), "cik": has_cik})
    ok = [r for r in rows if r["price_cov"] >= PRICE_MIN and r["cik"]]
    by_year: dict[str, list[int]] = {}
    for r in rows:
        y = r["removed"][:4]
        by_year.setdefault(y, [0, 0])
        by_year[y][0] += 1
        by_year[y][1] += r in ok
    share = len(ok) / len(rows) if rows else 0.0
    out = {
        "removed": len(rows),
        "usable": len(ok),
        "share": round(share, 3),
        "price_ok": sum(1 for r in rows if r["price_cov"] >= PRICE_MIN),
        "cik_ok": sum(1 for r in rows if r["cik"]),
        "by_year": {y: f"{v[1]}/{v[0]}" for y, v in sorted(by_year.items())},
        "verdict": "DATA_OK" if share >= GATE else "DATA_INSUFFICIENT",
        "no_price_or_cik": [r["ticker"] for r in rows if r not in ok][:80],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(
        json.dumps({**out, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
