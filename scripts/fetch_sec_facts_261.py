"""문서 261 자료 — SEC companyfacts 에서 유통 시가총액 · 매출 · 영업이익만 뽑아 저장 (수익 안 봄).

사용 예::

    python -m scripts.fetch_sec_facts_261   # data/research/sec_facts_261/<종목>.json

* 종목: 113 유니버스(``reports/sb_breakout/audit.json`` kept_symbols).
* ``dei:EntityPublicFloat``(달러, 10-K 표지 — 보통 2 분기 말 기준) · 매출(여러 태그) ·
  ``OperatingIncomeLoss``.
  각 값의 기간(start · end) · 공시일(filed) · 양식(form)을 그대로 둔다 — 공시일 전에는 쓰지 않는다.
* ``.env`` ``SEC_CONTACT_EMAIL`` 을 연락처로(SEC 규칙), 초당 10 회 아래.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import read_env_file  # noqa: E402

OUT = REPO / "data" / "research" / "sec_facts_261"
AUDIT = REPO / "reports" / "sb_breakout" / "audit.json"
REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
OPINC_TAG = "OperatingIncomeLoss"


def _rows(facts: dict[str, Any], ns: str, tag: str) -> list[dict[str, Any]]:
    units = ((facts.get(ns) or {}).get(tag) or {}).get("units") or {}
    out = []
    for vals in units.values():
        for v in vals:
            out.append(
                {
                    k: v.get(k)
                    for k in ("start", "end", "val", "filed", "form", "fp", "fy")
                    if k in v
                }
            )
    return out


def extract(data: dict[str, Any]) -> dict[str, Any]:
    facts = data.get("facts") or {}
    return {
        "float": _rows(facts, "dei", "EntityPublicFloat"),
        "revenue": {t: r for t in REVENUE_TAGS if (r := _rows(facts, "us-gaap", t))},
        "opinc": _rows(facts, "us-gaap", OPINC_TAG),
    }


def main() -> int:
    email = read_env_file(REPO / ".env").get("SEC_CONTACT_EMAIL", "").strip()
    if not email:
        raise SystemExit("SEC_CONTACT_EMAIL 없음")
    hdr = {"User-Agent": f"AutoTrading research 261 {email}", "Accept-Encoding": "gzip, deflate"}
    syms = json.loads(AUDIT.read_text(encoding="utf-8"))["kept_symbols"]
    OUT.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    with httpx.Client(timeout=60.0, headers=hdr) as c:
        cmap = c.get("https://www.sec.gov/files/company_tickers.json").json()
        cik = {v["ticker"].upper(): int(v["cik_str"]) for v in cmap.values()}
        for sym in syms:
            path = OUT / f"{sym}.json"
            if path.exists():
                continue
            key = sym.replace(".", "-").upper()
            if key not in cik:
                missing.append(sym)
                continue
            time.sleep(0.15)
            try:
                r = c.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[key]:010d}.json")
                r.raise_for_status()
                path.write_text(json.dumps(extract(r.json())), encoding="utf-8")
            except (httpx.HTTPError, ValueError) as exc:
                missing.append(f"{sym}:{type(exc).__name__}")
    (OUT / "_missing.json").write_text(json.dumps(missing), encoding="utf-8")
    print(json.dumps({"symbols": len(syms), "missing": missing}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
