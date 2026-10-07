"""문서 162 — 테마 주도주(시가총액 기준)용 시가총액 자료 받기. 무료 공개 자료만, 수익 계산 없음.

사용 예::

    python -m scripts.fetch_theme_caps kr       # KRX 일별 파일 → 테마 종목 시가총액 (월말만)
    $env:SEC_UA = "이름 연락처메일"; python -m scripts.fetch_theme_caps us   # SEC + Yahoo

* 국장: ``data/research/krx/{stk,ksq}/*.csv.gz`` 의 ``MKTCAP`` (그날 공식 시가총액, PIT).
  유가증권시장 파일은 2015-01 부터라 국장 시가총액은 2015 년부터다.
* 미장: SEC companyfacts 의 ``dei:EntityCommonStockSharesOutstanding`` (없으면
  ``us-gaap:CommonStockSharesOutstanding``) — 값 · 기준일(end) · 제출일(filed).
  Yahoo chart 의 분할 기록으로 분할을 맞춘다. SEC 는 연락처가 있는 User-Agent 를 요구한다 —
  환경 변수 ``SEC_UA`` 로만 받고 파일에는 남기지 않는다.
* 저장: ``data/research/theme_leaders/`` (Git 제외).
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "theme_leaders"
KRX = REPO / "data" / "research" / "krx"
SPEC = REPO / "docs" / "research" / "theme-leaders-162.json"
YAHOO_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def _get(url: str, ua: str) -> bytes | None:
    for k in range(4):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": ua, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 + 3 * k)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 + 3 * k)
    return None


def kr() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    codes = {c for ms in spec["KR"].values() for c, _n, _f in ms}
    OUT.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict[str, int]] = defaultdict(dict)  # date → code → cap
    for mkt in ("stk", "ksq"):
        for p in sorted((KRX / mkt).glob("*.csv.gz")):
            d = p.name[:8]
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r["ISU_CD"] in codes and r["MKTCAP"]:
                        rows[d][r["ISU_CD"]] = int(float(r["MKTCAP"]))
    with (OUT / "kr_mktcap.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "code", "mktcap"])
        for d in sorted(rows):
            for c in sorted(rows[d]):
                w.writerow([d, c, rows[d][c]])
    print("kr dates", len(rows))


def us() -> None:
    ua = os.environ.get("SEC_UA")
    if not ua:
        raise SystemExit("SEC_UA 환경 변수가 필요하다 (SEC 정책: 연락처가 있는 User-Agent)")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    syms = sorted({s for ms in spec["US"].values() for s, _n, _f in ms})
    d = OUT / "us"
    d.mkdir(parents=True, exist_ok=True)
    body = _get("https://www.sec.gov/files/company_tickers.json", ua)
    if body is None:
        raise SystemExit("SEC 티커 목록 실패")
    cik = {v["ticker"]: int(v["cik_str"]) for v in json.loads(body).values()}
    for s in syms:
        p = d / f"{s}.json"
        if p.exists():
            continue
        out: dict[str, object] = {"symbol": s}
        if s in cik:
            facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[s]:010d}.json", ua)
            if facts:
                f = json.loads(facts)["facts"]
                for ns, tag in (
                    ("dei", "EntityCommonStockSharesOutstanding"),
                    ("us-gaap", "CommonStockSharesOutstanding"),
                ):
                    units = f.get(ns, {}).get(tag, {}).get("units", {}).get("shares", [])
                    out[f"{ns}:{tag}"] = [
                        [u["end"], u["filed"], u["val"]] for u in units if "filed" in u
                    ]
            time.sleep(0.15)  # SEC 초당 10 회 제한보다 느리게
        y = _get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=1167609600&period2=1791331200"
            "&interval=1d&events=splits",
            YAHOO_UA,
        )
        if y:
            res = json.loads(y)["chart"]["result"][0]
            spl = res.get("events", {}).get("splits", {})
            out["splits"] = sorted(
                [v["date"], v["numerator"] / v["denominator"]] for v in spl.values()
            )
            q = res["indicators"]["quote"][0]
            out["close_split_adj"] = [
                [t, c]
                for t, c in zip(res.get("timestamp", []), q.get("close", []), strict=True)
                if c
            ]
        p.write_text(json.dumps(out), encoding="utf-8")
        print(
            s,
            len(out.get("dei:EntityCommonStockSharesOutstanding", [])),
            len(out.get("splits", [])),
            flush=True,
        )
        time.sleep(0.3)


if __name__ == "__main__":
    {"kr": kr, "us": us}[sys.argv[1]]()
