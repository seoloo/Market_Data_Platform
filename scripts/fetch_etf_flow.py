"""IDEAS 23·24 — 미국 현물 BTC ETF 흐름 자료 받기. 무료 공개 자료만, 수익 계산 없음.

사용 예::

    python -m scripts.fetch_etf_flow farside    # Farside 전체 표(2024-01-11 ~) 지금 버전
    python -m scripts.fetch_etf_flow wayback    # Wayback 의 Farside 과거 버전 (공개 시각·수정 감사)
    python -m scripts.fetch_etf_flow finra      # FINRA 일별 공매도 거래량(CNMS) 중 ETF 행
    python -m scripts.fetch_etf_flow prices     # ETF 일봉(달러 가중용, Yahoo chart)

* 저장: ``data/research/etf_flow/`` (Git 제외). 이미 받은 파일은 건너뛴다.
* Farside 는 공개 시각·수정 이력을 주지 않는다. Wayback 스냅숏은 "그 시각에 보이던 값"의
  표본 증거다(완전한 이력이 아님).
* FINRA 파일의 ``Last-Modified`` 는 마지막 수정 시각이다. 원본 게시 뒤 고쳐졌으면 더 늦은
  값이 찍히므로, 이를 가용 시각으로 쓰면 보수적이다.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "etf_flow"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
FARSIDE_ALL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
FARSIDE_PAGES = {"btc": "farside.co.uk/btc/", "all": "farside.co.uk/bitcoin-etf-flow-all-data/"}
FINRA = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
ETFS = (
    "IBIT",
    "FBTC",
    "BITB",
    "ARKB",
    "BTCO",
    "EZBC",
    "BRRR",
    "HODL",
    "BTCW",
    "MSBT",
    "GBTC",
    "BTC",
    "DEFI",
)
"""현물 BTC ETF(Farside 열 + DEFI). 선물·레버리지·인버스 ETF 는 넣지 않는다.
``BTC`` 는 Grayscale Bitcoin Mini Trust(2024-07-31 ~), ``DEFI`` 는 Hashdex(2024-03 현물 전환)."""
FIRST, LAST = date(2024, 1, 11), date(2026, 10, 6)


def _get(
    url: str, *, referer: str | None = None, tries: int = 4
) -> tuple[int, bytes, dict[str, str]]:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    for k in range(tries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as r:
                return r.status, r.read(), {h.lower(): v for h, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return e.code, b"", {}
            time.sleep(2 + 3 * k)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 + 3 * k)
    return 0, b"", {}


def farside() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    st, body, _ = _get(FARSIDE_ALL, referer="https://farside.co.uk/btc/")
    if st != 200:
        raise SystemExit(f"Farside {st}")
    (OUT / "farside_all.html").write_bytes(body)
    (OUT / "farside_all.meta.json").write_text(
        json.dumps({"fetched_utc": datetime.now(UTC).isoformat()})
    )
    print("farside", len(body))


def wayback() -> None:
    d = OUT / "wayback"
    d.mkdir(parents=True, exist_ok=True)
    for key, page in FARSIDE_PAGES.items():
        cdx = f"http://web.archive.org/cdx/search/cdx?url={page}&output=json&fl=timestamp,digest&filter=statuscode:200&collapse=digest"
        st, body, _ = _get(cdx)
        rows = json.loads(body)[1:] if st == 200 and body else []

        def one(ts: str, key: str = key, page: str = page) -> int:
            p = d / f"{key}_{ts}.html"
            if p.exists():
                return 0
            st, html, _ = _get(f"http://web.archive.org/web/{ts}id_/https://{page}")
            if st == 200 and html:
                p.write_bytes(html)
                return 1
            return 0

        stamps = [ts for ts, _digest in rows]
        if "--slow" in sys.argv:  # Wayback 이 연결을 끊으면: 하나씩, 최근 것부터, 4 초 간격
            got = 0
            for ts in reversed(stamps):
                got += one(ts)
                time.sleep(4)
        else:
            with ThreadPoolExecutor(4) as ex:
                got = sum(ex.map(one, stamps))
        print(key, len(rows), "new", got, flush=True)


def finra() -> None:
    d = OUT / "finra"
    d.mkdir(parents=True, exist_ok=True)
    out = d / "cnms_btc_etf.csv"
    meta = d / "cnms_meta.csv"
    done = set()
    if meta.exists():
        with meta.open() as f:
            done = {r["date"] for r in csv.DictReader(f)}
    new_meta = not meta.exists()
    with out.open("a", newline="") as fo, meta.open("a", newline="") as fm:
        wo, wm = csv.writer(fo), csv.writer(fm)
        if new_meta:
            wo.writerow(
                ["date", "symbol", "short_volume", "short_exempt_volume", "total_volume", "market"]
            )
            wm.writerow(["date", "status", "last_modified_utc"])
        days = []
        day = FIRST
        while day <= LAST:
            if day.weekday() < 5 and day.isoformat() not in done:
                days.append(day)
            day += timedelta(days=1)

        def one(d: date) -> tuple[int, bytes, dict[str, str]]:
            return _get(FINRA.format(ymd=d.strftime("%Y%m%d")))

        with ThreadPoolExecutor(6) as ex:
            for day, (st, body, hdr) in zip(days, ex.map(one, days), strict=True):
                lm = ""
                if st == 200:
                    lm = (
                        parsedate_to_datetime(hdr["last-modified"]).astimezone(UTC).isoformat()
                        if "last-modified" in hdr
                        else ""
                    )
                    for line in body.decode("utf-8", "replace").splitlines()[1:]:
                        cols = line.split("|")
                        if len(cols) >= 6 and cols[1] in ETFS:
                            wo.writerow([day.isoformat(), *cols[1:6]])
                if st in (200, 403, 404):
                    wm.writerow([day.isoformat(), st, lm])
                fo.flush()
                fm.flush()
                print(day.isoformat(), st, flush=True)
    print("finra done")


def prices() -> None:
    d = OUT / "prices"
    d.mkdir(parents=True, exist_ok=True)
    p1 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    p2 = int(datetime(2026, 10, 8, tzinfo=UTC).timestamp())
    for s in ETFS:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1={p1}&period2={p2}&interval=1d"
        st, body, _ = _get(url)
        if st != 200:
            print(s, st)
            continue
        res = json.loads(body)["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        with (d / f"{s}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            for ts, c in zip(res.get("timestamp", []), q.get("close", []), strict=True):
                if c is not None:
                    # 미국 장 시각 기준 거래일 (UTC-4/-5 → 날짜는 UTC 13:30 이후라 같은 날)
                    w.writerow([datetime.fromtimestamp(ts, UTC).date().isoformat(), f"{c:.6f}"])
        print(s, len(res.get("timestamp", [])))
        time.sleep(0.5)


def funding() -> None:
    """DB 월 파일 뒤(2026-10-01 ~)의 BTCUSDT funding — Binance 공개 REST."""
    start = int(datetime(2026, 9, 25, tzinfo=UTC).timestamp() * 1000)
    st, body, _ = _get(
        f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime={start}&limit=1000"
    )
    if st != 200:
        raise SystemExit(f"funding {st}")
    with (OUT / "funding_tail.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["funding_ms", "rate"])
        for r in json.loads(body):
            w.writerow([r["fundingTime"], r["fundingRate"]])
    print("funding", len(json.loads(body)))


if __name__ == "__main__":
    {"farside": farside, "wayback": wayback, "finra": finra, "prices": prices, "funding": funding}[
        sys.argv[1]
    ]()
