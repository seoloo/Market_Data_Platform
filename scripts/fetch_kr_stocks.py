"""국장 LT 재현(문서 141) 자료 받기 — 유가증권시장 보통주 일봉(상장폐지 포함)·KOSPI 지수.

사용 예::

    python -m scripts.fetch_kr_stocks lists    # KIND 상장·상장폐지 목록 + 한국투자증권 종목 정보
    python -m scripts.fetch_kr_stocks prices   # 보통주 수정주가 일봉 (이어 받기 가능)
    python -m scripts.fetch_kr_stocks index    # KOSPI 지수 일봉
    python -m scripts.fetch_kr_stocks flows    # 종목별 투자자 매매(외국인·기관 순매수), 문서 142
    python -m scripts.fetch_kr_stocks raw      # 비수정 시·고·저·종·거래량 (실적 연구)
    python -m scripts.fetch_kr_stocks dividends  # 예탁원 배당일정 (기준일·주당 배당금·지급일)

* 목록: KIND(kind.krx.co.kr) 유가증권시장 상장법인 목록과 2008-01-01 뒤 상장폐지 현황
  — 로그인 없이 받는다.
* 종목 정보·일봉: 한국투자증권 Open API **조회만**(:class:`KisOverseasReadOnly` 의 조회 경로).
  주문 코드는 없다. 수정주가(``FID_ORG_ADJ_PRC=0``), 한 번에 최대 100 거래일(지수 50),
  빈 응답이나 시작일에 닿을 때까지 거꾸로 받는다, 2007-01-01 ~ 2026-08-31.
* 저장: ``data/research/kr_stocks/`` (git 제외). 수익은 계산하지 않는다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "kr_stocks"
PRICES = DATA / "prices"
START, END = "20070101", "20260831"
KIND_LISTED = "https://kind.krx.co.kr/corpgeneral/corpList.do"
KIND_DELISTED = "https://kind.krx.co.kr/investwarn/delcompany.do"
DAILY = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
INDEX = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
INFO = "/uapi/domestic-stock/v1/quotations/search-stock-info"
INVESTOR = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
FLOWS = DATA / "flows"
FLOW_START = "20080101"
RAW = DATA / "raw"
RAW_START = "20170101"
"""비수정 일봉 시작 — 실적 연구 신호(2018-04~) 앞 1 년(63 일 거래대금·전날 종가)."""
FLOW_FIELDS = (
    "frgn_ntby_tr_pbmn",
    "orgn_ntby_tr_pbmn",
    "prsn_ntby_tr_pbmn",
    "acml_tr_pbmn",
)
"""순매수 금액(백만 원)·거래대금(원). 한 번에 30 거래일, 2006 년부터·상장폐지 종목도 받아짐."""
DIVIDEND = "/uapi/domestic-stock/v1/ksdinfo/dividend"
DIVIDENDS = DATA / "dividends"
DIV_BLOCKS = (("20150101", "20181231"), ("20190101", "20221231"), ("20230101", END))
"""배당일정은 기간을 나눠 묻는다 (한 응답에 다 오는지 몰라서 — 종목당 3 번)."""
PAUSE = 0.06
COMMON = "101"
"""한국투자증권 ``stck_kind_cd`` 보통주."""

Getter = Callable[[str, str, Mapping[str, str]], dict[str, Any]]


# ------------------------------------------------------------------ 해석 (순수)


def table_rows(text: str) -> list[list[str]]:
    """KIND 엑셀(HTML 표) 내려받기를 행 목록으로."""
    out: list[list[str]] = []
    for tr in re.findall(r"<tr.*?>(.*?)</tr>", text, re.S):
        cells = [
            html.unescape(re.sub(r"<.*?>", "", c)).strip()
            for c in re.findall(r"<t[dh].*?>(.*?)</t[dh]>", tr, re.S)
        ]
        if cells:
            out.append(cells)
    return out


def listed_codes(rows: Sequence[Sequence[str]]) -> list[str]:
    """상장법인 목록(머리: 회사명·시장구분·종목코드…)의 종목코드."""
    head = list(rows[0])
    i = head.index("종목코드")
    return [r[i].zfill(6) for r in rows[1:] if len(r) > i and r[i]]


def delisted_codes(rows: Sequence[Sequence[str]]) -> dict[str, str]:
    """상장폐지 현황(머리: 번호·회사명·종목코드·폐지일자…) → {종목코드: 폐지일자}."""
    head = list(rows[0])
    i, j = head.index("종목코드"), head.index("폐지일자")
    return {r[i].zfill(6): r[j] for r in rows[1:] if len(r) > j and r[i]}


def daily_rows(output2: Sequence[Mapping[str, Any]]) -> list[tuple[str, float, float, float]]:
    """한국투자증권 기간별 시세 → (날짜, 종가, 거래량, 거래대금), 오래된 순. 종가 0 은 버린다."""
    out = []
    for r in output2:
        d = str(r.get("stck_bsop_date") or "")
        c = float(r.get("stck_clpr") or 0)
        if d and c > 0:
            out.append((d, c, float(r.get("acml_vol") or 0), float(r.get("acml_tr_pbmn") or 0)))
    return sorted(set(out))


def flow_rows(output2: Sequence[Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """종목별 투자자 매매(일별) → (날짜, 외국인·기관·개인 순매수 금액, 거래대금), 오래된 순."""
    out = []
    for r in output2:
        d = str(r.get("stck_bsop_date") or "")
        if d:
            out.append((d, *(str(r.get(f) or "0") for f in FLOW_FIELDS)))
    return sorted(set(out))


def raw_rows(output2: Sequence[Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """기간별 시세(원주가) → (날짜, 시가, 고가, 저가, 종가, 거래량, 거래대금, 락 구분), 오래된 순.

    종가 0 은 버린다. 거래량 0 인 날(거래정지)은 그대로 남긴다 — 거래 없음은 쓰는 쪽에서 판단."""
    out = []
    for r in output2:
        d = str(r.get("stck_bsop_date") or "")
        c = str(r.get("stck_clpr") or "0")
        if not d or float(c) <= 0:
            continue
        out.append(
            (
                d,
                str(r.get("stck_oprc") or "0"),
                str(r.get("stck_hgpr") or "0"),
                str(r.get("stck_lwpr") or "0"),
                c,
                str(r.get("acml_vol") or "0"),
                str(r.get("acml_tr_pbmn") or "0"),
                str(r.get("flng_cls_code") or ""),
            )
        )
    return sorted(set(out))


def dividend_rows(output: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """예탁원 배당일정 응답 → 보통주 현금 배당 줄 (기준일 순, 같은 기준일·구분 한 번)."""
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for r in output:
        if str(r.get("stk_kind", "보통")) != "보통":
            continue
        rec = str(r.get("record_date", ""))
        if not re.fullmatch(r"\d{8}", rec):
            continue
        row = {
            "record_date": rec,
            "kind": str(r.get("divi_kind", "")),
            "dps": str(r.get("per_sto_divi_amt", "0")).replace(",", "") or "0",
            "pay_date": str(r.get("divi_pay_dt", "")).replace("/", ""),
            "face": str(r.get("face_val", "")),
        }
        seen[(rec, row["kind"])] = row
    return [seen[k] for k in sorted(seen)]


def window(info: Mapping[str, str], start: str = START, end: str = END) -> tuple[str, str] | None:
    """받을 기간: 유가증권시장 상장일 ~ 상장폐지일(또는 끝)."""
    lo = max(start, info.get("lstg") or start)
    abol = info.get("abol") or ""
    hi = min(end, abol) if abol else end
    return (lo, hi) if lo <= hi else None


# ------------------------------------------------------------------ 받기


def _kis() -> Any:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    return KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )


def fetch_lists() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    head = {"User-Agent": "Mozilla/5.0"}
    listed = httpx.post(
        KIND_LISTED,
        data={"method": "download", "searchType": "13", "marketType": "stockMkt"},
        headers=head,
        timeout=60,
    )
    delisted = httpx.post(
        KIND_DELISTED,
        data={
            "method": "searchDelCompanySub",
            "currentPageSize": "5000",
            "pageIndex": "1",
            "orderMode": "2",
            "orderStat": "D",
            "marketType": "1",
            "fromDate": "2008-01-01",
            "toDate": "2026-10-05",
            "forward": "delcompany_down",
        },
        headers=head,
        timeout=60,
    )
    (DATA / "kind_listed.html").write_bytes(listed.content)
    (DATA / "kind_delisted.html").write_bytes(delisted.content)
    codes = listed_codes(table_rows(listed.content.decode("euc-kr", "replace")))
    gone = delisted_codes(table_rows(delisted.content.decode("euc-kr", "replace")))
    kis = _kis()
    info: dict[str, dict[str, str]] = {}
    try:
        for code in sorted(set(codes) | set(gone)):
            o = kis._get(INFO, "CTPF1002R", {"PRDT_TYPE_CD": "300", "PDNO": code}).get("output")
            o = o or {}
            info[code] = {
                "name": str(o.get("prdt_abrv_name", "")),
                "kind": str(o.get("stck_kind_cd", "")),
                "market": str(o.get("mket_id_cd", "")),
                "sector": str(o.get("idx_bztp_mcls_cd_name", "")),
                "ksic": str(o.get("std_idst_clsf_cd", "")),
                "ksic_name": str(o.get("std_idst_clsf_cd_name", "")),
                "lstg": str(o.get("scts_mket_lstg_dt", "")),
                "abol": str(o.get("lstg_abol_dt", "")),
                "kind_delisted": gone.get(code, ""),
                "kind_listed": "1" if code in codes else "",
            }
            time.sleep(PAUSE)
    finally:
        kis.close()
    (DATA / "info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    common = [c for c, v in info.items() if v["kind"] == COMMON]
    print(f"상장 {len(codes)} · 폐지 {len(gone)} · 종목 정보 {len(info)} · 보통주 {len(common)}")
    return 0


def _pages(get: Getter, code: str, lo: str, hi: str) -> list[tuple[str, float, float, float]]:
    rows: list[tuple[str, float, float, float]] = []
    cursor = hi
    while cursor >= lo:
        out = (
            get(
                DAILY,
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": lo,
                    "FID_INPUT_DATE_2": cursor,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            ).get("output2")
            or []
        )
        page = daily_rows(out)
        if not page:
            break
        rows.extend(page)
        if page[0][0] <= lo:
            break
        first = date(int(page[0][0][:4]), int(page[0][0][4:6]), int(page[0][0][6:]))
        cursor = (first - timedelta(days=1)).strftime("%Y%m%d")
        time.sleep(PAUSE)
    return sorted(set(rows))


def fetch_prices() -> int:
    info = json.loads((DATA / "info.json").read_text(encoding="utf-8"))
    PRICES.mkdir(parents=True, exist_ok=True)
    todo = [c for c, v in sorted(info.items()) if v["kind"] == COMMON]
    kis = _kis()
    done = failed = 0
    try:
        for k, code in enumerate(todo, 1):
            path = PRICES / f"{code}.csv"
            span = window(info[code])
            if path.exists() or span is None:
                continue
            try:
                rows = _pages(kis._get, code, *span)
            except Exception as exc:  # 한 종목 실패가 전체를 멈추지 않게
                failed += 1
                print(f"[{k}/{len(todo)}] {code} 실패: {type(exc).__name__}", flush=True)
                continue
            text = "date,close,volume,value\n" + "".join(
                f"{d},{c:g},{v:g},{a:g}\n" for d, c, v, a in rows
            )
            path.write_text(text, encoding="utf-8", newline="\n")
            done += 1
            if k % 25 == 0:
                print(f"[{k}/{len(todo)}] {code} {len(rows)} 일", flush=True)
    finally:
        kis.close()
    print(f"받음 {done} · 실패 {failed} · 대상 {len(todo)}")
    return 0 if failed == 0 else 1


def _flow_pages(get: Getter, code: str, lo: str, hi: str) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    cursor = hi
    while cursor >= lo:
        out = (
            get(
                INVESTOR,
                "FHPTJ04160001",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": cursor,
                    "FID_ORG_ADJ_PRC": "",
                    "FID_ETC_CLS_CODE": "",
                },
            ).get("output2")
            or []
        )
        page = flow_rows(out)
        if not page:
            break
        rows.extend(r for r in page if r[0] >= lo)
        if page[0][0] <= lo:
            break
        first = date(int(page[0][0][:4]), int(page[0][0][4:6]), int(page[0][0][6:]))
        cursor = (first - timedelta(days=1)).strftime("%Y%m%d")
        time.sleep(PAUSE)
    return sorted(set(rows))


def fetch_flows(shard: int = 0, shards: int = 1) -> int:
    """종목군에 한 번이라도 들어온 종목(``ever_universe.json``)만 받는다.

    ``shard``/``shards`` 로 나눠 여러 프로세스가 동시에 받을 수 있다(초당 한도 20 건 안)."""
    info = json.loads((DATA / "info.json").read_text(encoding="utf-8"))
    every = json.loads((DATA / "ever_universe.json").read_text(encoding="utf-8"))
    todo = [c for i, c in enumerate(every) if i % shards == shard]
    FLOWS.mkdir(parents=True, exist_ok=True)
    kis = _kis()
    done = failed = 0
    try:
        for k, code in enumerate(todo, 1):
            path = FLOWS / f"{code}.csv"
            span = window(info[code], start=FLOW_START)
            if path.exists() or span is None:
                continue
            try:
                rows = _flow_pages(kis._get, code, *span)
            except Exception as exc:  # 한 종목 실패가 전체를 멈추지 않게
                failed += 1
                print(f"[{k}/{len(todo)}] {code} 실패: {type(exc).__name__}", flush=True)
                continue
            path.write_text(
                "date," + ",".join(FLOW_FIELDS) + "\n" + "".join(",".join(r) + "\n" for r in rows),
                encoding="utf-8",
                newline="\n",
            )
            done += 1
            if k % 25 == 0:
                print(f"[{k}/{len(todo)}] {code} {len(rows)} 일", flush=True)
    finally:
        kis.close()
    print(f"받음 {done} · 실패 {failed} · 대상 {len(todo)}")
    return 0 if failed == 0 else 1


def _raw_pages(get: Getter, code: str, lo: str, hi: str) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    cursor = hi
    while cursor >= lo:
        out = (
            get(
                DAILY,
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": lo,
                    "FID_INPUT_DATE_2": cursor,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "1",  # 1 = 원주가(비수정)
                },
            ).get("output2")
            or []
        )
        page = raw_rows(out)
        if not page:
            break
        rows.extend(page)
        if page[0][0] <= lo:
            break
        first = date(int(page[0][0][:4]), int(page[0][0][4:6]), int(page[0][0][6:]))
        cursor = (first - timedelta(days=1)).strftime("%Y%m%d")
        time.sleep(PAUSE)
    return sorted(set(rows))


RAW_FIELDS = ("open", "high", "low", "close", "volume", "value", "lock")


def fetch_raw(shard: int = 0, shards: int = 1) -> int:
    """종목군에 한 번이라도 들어온 종목(``ever_universe.json``)의 비수정 일봉, 2017-01 ~."""
    info = json.loads((DATA / "info.json").read_text(encoding="utf-8"))
    every = json.loads((DATA / "ever_universe.json").read_text(encoding="utf-8"))
    todo = [c for i, c in enumerate(every) if i % shards == shard]
    RAW.mkdir(parents=True, exist_ok=True)
    kis = _kis()
    done = failed = 0
    try:
        for k, code in enumerate(todo, 1):
            path = RAW / f"{code}.csv"
            span = window(info[code], start=RAW_START)
            if path.exists() or span is None:
                continue
            try:
                rows = _raw_pages(kis._get, code, *span)
            except Exception as exc:  # 한 종목 실패가 전체를 멈추지 않게
                failed += 1
                print(f"[{k}/{len(todo)}] {code} 실패: {type(exc).__name__}", flush=True)
                continue
            path.write_text(
                "date," + ",".join(RAW_FIELDS) + "\n" + "".join(",".join(r) + "\n" for r in rows),
                encoding="utf-8",
                newline="\n",
            )
            done += 1
            if k % 25 == 0:
                print(f"[{k}/{len(todo)}] {code} {len(rows)} 일", flush=True)
    finally:
        kis.close()
    print(f"받음 {done} · 실패 {failed} · 대상 {len(todo)}")
    return 0 if failed == 0 else 1


def index_pages(get: Getter, lo: str, hi: str) -> list[tuple[str, float]]:
    """KOSPI 지수 일봉 (한 번에 최대 50 일, 거꾸로)."""
    rows: list[tuple[str, float]] = []
    cursor = hi
    while cursor >= lo:
        out = (
            get(
                INDEX,
                "FHKUP03500100",
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": "0001",
                    "FID_INPUT_DATE_1": lo,
                    "FID_INPUT_DATE_2": cursor,
                    "FID_PERIOD_DIV_CODE": "D",
                },
            ).get("output2")
            or []
        )
        page = sorted(
            (str(r["stck_bsop_date"]), float(r["bstp_nmix_prpr"]))
            for r in out
            if r.get("stck_bsop_date") and float(r.get("bstp_nmix_prpr") or 0) > 0
        )
        if not page:
            break
        rows.extend(p for p in page if p[0] >= lo)
        if page[0][0] <= lo:
            break
        first = date(int(page[0][0][:4]), int(page[0][0][4:6]), int(page[0][0][6:]))
        cursor = (first - timedelta(days=1)).strftime("%Y%m%d")
        time.sleep(PAUSE)
    return sorted(set(rows))


def fetch_index() -> int:
    kis = _kis()
    try:
        rows = index_pages(kis._get, START, END)
    finally:
        kis.close()
    rows = sorted(set(rows))
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "kospi.csv").write_text(
        "date,close\n" + "".join(f"{d},{c:g}\n" for d, c in rows), encoding="utf-8", newline="\n"
    )
    print(f"KOSPI {len(rows)} 일 ({rows[0][0]} ~ {rows[-1][0]})")
    return 0


def fetch_dividends() -> int:
    """2015 ~ 상장해 있던 모든 보통주의 예탁원 배당일정 (실적 연구 총수익, 관문 문서 §5)."""
    info = json.loads((DATA / "info.json").read_text(encoding="utf-8"))
    todo = sorted(c for c, v in info.items() if v.get("kind") == COMMON)
    DIVIDENDS.mkdir(parents=True, exist_ok=True)
    kis = _kis()
    done = failed = 0
    try:
        for k, code in enumerate(todo, 1):
            path = DIVIDENDS / f"{code}.json"
            span = window(info[code], start=DIV_BLOCKS[0][0])
            if path.exists() or span is None:
                continue
            rows: list[Mapping[str, Any]] = []
            try:
                for lo, hi in DIV_BLOCKS:
                    if hi < span[0] or lo > span[1]:
                        continue
                    got = kis._get(
                        DIVIDEND,
                        "HHKDB669102C0",
                        {"CTS": "", "GB1": "0", "F_DT": lo, "T_DT": hi, "SHT_CD": code,
                         "HIGH_GB": ""},
                    )  # fmt: skip
                    rows += list(got.get("output1") or [])
                    time.sleep(PAUSE)
            except Exception as exc:  # 한 종목 실패가 전체를 멈추지 않게
                failed += 1
                print(f"[{k}/{len(todo)}] {code} 실패: {type(exc).__name__}", flush=True)
                continue
            path.write_text(json.dumps(dividend_rows(rows), ensure_ascii=False), encoding="utf-8")
            done += 1
            if k % 100 == 0:
                print(f"[{k}/{len(todo)}] {code}", flush=True)
    finally:
        kis.close()
    print(f"받음 {done} · 실패 {failed} · 대상 {len(todo)}")
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("lists", "prices", "index", "flows", "raw", "dividends"))
    parser.add_argument("--shard", default="0/1", help="flows·raw 를 나눠 받기: i/n")
    args = parser.parse_args(argv)
    if args.stage in ("flows", "raw"):
        i, n = (int(x) for x in args.shard.split("/"))
        return (fetch_flows if args.stage == "flows" else fetch_raw)(i, n)
    stages = {
        "lists": fetch_lists,
        "prices": fetch_prices,
        "index": fetch_index,
        "flows": fetch_flows,
        "dividends": fetch_dividends,
    }
    return stages[args.stage]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
