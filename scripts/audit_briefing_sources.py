"""IDEAS 29 (하락 브리핑 정보 도구) Phase 0 — 무료 출처 접속 점검. 내용을 저장 · 분석하지 않는다.

사용 예::

    python -m scripts.audit_briefing_sources

* 공개 주소에 일반 브라우저 머리말로 한 번씩 GET 하고 상태 · 형식 · 크기만 적는다.
* 개인 정보(이메일 등)를 머리말에 넣지 않는다 — 그래서 SEC 처럼 연락처를 요구하는 곳은
  거부가 정상이다(사용자 결정 항목).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import httpx

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}
SOURCES = {
    "cboe_vix_daily_csv": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "fred_vixcls_csv": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS",
    "invesco_qqq_holdings": "https://www.invesco.com/us/financial-products/etfs/holdings/main/"
    "holdings/0?audienceType=Investor&action=download&ticker=QQQ",
    "ssga_spy_holdings_xlsx": "https://www.ssga.com/us/en/intermediary/library-content/products/"
    "fund-data/etfs/us/holdings-daily-us-en-spy.xlsx",
    "sec_company_tickers": "https://www.sec.gov/files/company_tickers.json",
    "bls_cpi_schedule": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "fed_press_rss": "https://www.federalreserve.gov/feeds/press_all.xml",
    "kind_krx": "https://kind.krx.co.kr/",
}


def main() -> int:
    out: dict[str, object] = {"at": datetime.now(UTC).isoformat()}
    for name, url in SOURCES.items():
        try:
            r = httpx.get(url, headers=UA, timeout=20, follow_redirects=True)
            out[name] = {
                "status": r.status_code,
                "type": r.headers.get("content-type", "")[:40],
                "bytes": len(r.content),
            }
        except httpx.HTTPError as exc:
            out[name] = {"status": "ERR", "type": type(exc).__name__}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
