"""코스닥 시험(문서 154) 자료 — 그날 코스닥에 있던 종목(KRX 일별)으로 종목군 후보를 정하고 받는다.

사용 예::

    python -m scripts.fetch_kq_stocks universe   # KRX 일별(2010 ~) → 월말 거래대금 상위 300 합집합
    python -m scripts.fetch_kq_stocks info       # 한국투자증권 종목정보(업종·보통주 여부)
    python -m scripts.fetch_kq_stocks prices     # 수정주가 (코스닥에 있던 기간 + 앞 300 일)
    python -m scripts.fetch_kq_stocks index      # 코스닥 지수 일봉
    python -m scripts.fetch_kq_stocks flows --shard 0/3   # 종목별 투자자 매매 (나눠 받기)

* 코스닥 소속은 **KRX 일별 매매정보**(그날 상장된 모든 종목, 뒤에 폐지·유가증권시장으로 옮긴
  종목 포함)로만 정한다 — 한국투자증권 종목정보는 지금 시장만 알려 준다(셀트리온·카카오 등).
* 후보는 넉넉하게(300): 실제 종목군(141 정의 상위 200, 1·3·6 달 수익 자격)은 판에서 다시 고른다.
* 저장 ``data/research/kq_stocks/`` (git 제외). 한국투자증권은 조회만. 수익은 계산하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.fetch_kr_stocks import (
    FLOW_FIELDS,
    INDEX,
    INFO,
    PAUSE,
    _flow_pages,
    _kis,
    _pages,
)

REPO = Path(__file__).resolve().parent.parent
KRX = REPO / "data" / "research" / "krx" / "ksq"
DATA = REPO / "data" / "research" / "kq_stocks"
PRICES, FLOWS = DATA / "prices", DATA / "flows"
START, END = "20100101", "20260831"
CANDIDATES, LIQ_DAYS = 300, 63
PRICE_LEAD, FLOW_LEAD = 300, 120
"""코스닥 첫날보다 이만큼(달력 일) 앞부터 — 1·3·6 달 수익 · 20·60 일 수급용."""
KOSDAQ_INDEX = "1001"


def krx_days() -> list[str]:
    return sorted(p.name[:8] for p in KRX.glob("*.csv.gz") if START <= p.name[:8] <= END)


def krx_rows(day: str) -> list[dict[str, str]]:
    text = gzip.decompress((KRX / f"{day}.csv.gz").read_bytes()).decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def candidates(
    days: list[str], value: dict[str, dict[int, float]]
) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """월말마다 그날 코스닥에 있던 종목 중 직전 63 거래일 평균 거래대금 상위 300 의 합집합 (순수).

    ``value``{종목: {거래일 색인: 거래대금}} — 그날 소속이면 키가 있다(거래 없으면 0)."""
    chosen: set[str] = set()
    for t in range(len(days) - 1):
        if days[t + 1][:6] == days[t][:6] or t < LIQ_DAYS:
            continue
        lo = t - LIQ_DAYS + 1
        ranked = sorted(
            (-sum(v.get(k, 0.0) for k in range(lo, t + 1)) / LIQ_DAYS, s)
            for s, v in value.items()
            if t in v
        )
        chosen |= {s for _, s in ranked[:CANDIDATES]}
    span = {s: (days[min(value[s])], days[max(value[s])]) for s in chosen}
    return sorted(chosen), span


def build_universe() -> int:
    days = krx_days()
    value: dict[str, dict[int, float]] = defaultdict(dict)
    names: dict[str, str] = {}
    for t, d in enumerate(days):
        for r in krx_rows(d):
            value[r["ISU_CD"]][t] = float(r["ACC_TRDVAL"] or 0)
            names[r["ISU_CD"]] = r["ISU_NM"]
    chosen, span = candidates(days, value)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "candidates.json").write_text(
        json.dumps(
            {s: {"name": names[s], "first": span[s][0], "last": span[s][1]} for s in chosen},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"KRX 일 {len(days)} ({days[0]} ~ {days[-1]}) · 종목 {len(value)} · 후보 {len(chosen)}")
    return 0


def _cands() -> dict[str, dict[str, str]]:
    return json.loads((DATA / "candidates.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _shift(day: str, back: int) -> str:
    d = date(int(day[:4]), int(day[4:6]), int(day[6:])) - timedelta(days=back)
    return d.strftime("%Y%m%d")


def fetch_info() -> int:
    kis = _kis()
    info: dict[str, dict[str, str]] = {}
    try:
        for code in sorted(_cands()):
            o = kis._get(INFO, "CTPF1002R", {"PRDT_TYPE_CD": "300", "PDNO": code}).get("output")
            o = o or {}
            info[code] = {
                "name": str(o.get("prdt_abrv_name", "")),
                "kind": str(o.get("stck_kind_cd", "")),
                "market_now": str(o.get("mket_id_cd", "")),
                "ksic": str(o.get("std_idst_clsf_cd", "")),
                "ksic_name": str(o.get("std_idst_clsf_cd_name", "")),
            }
            time.sleep(PAUSE)
    finally:
        kis.close()
    (DATA / "info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    print(f"종목 정보 {len(info)} · 보통주 {sum(v['kind'] == '101' for v in info.values())}")
    return 0


def fetch_prices() -> int:
    PRICES.mkdir(parents=True, exist_ok=True)
    kis = _kis()
    done = failed = 0
    cands = _cands()
    try:
        for k, (code, c) in enumerate(sorted(cands.items()), 1):
            path = PRICES / f"{code}.csv"
            if path.exists():
                continue
            lo, hi = max("20090101", _shift(c["first"], PRICE_LEAD)), min(c["last"], END)
            try:
                rows = _pages(kis._get, code, lo, hi)
            except Exception as exc:  # 한 종목 실패가 전체를 멈추지 않게
                failed += 1
                print(f"[{k}/{len(cands)}] {code} 실패: {type(exc).__name__}", flush=True)
                continue
            path.write_text(
                "date,close,volume,value\n"
                + "".join(f"{d},{cl:g},{v:g},{a:g}\n" for d, cl, v, a in rows),
                encoding="utf-8",
                newline="\n",
            )
            done += 1
            if k % 50 == 0:
                print(f"[{k}/{len(cands)}] {code} {len(rows)} 일", flush=True)
    finally:
        kis.close()
    print(f"받음 {done} · 실패 {failed} · 대상 {len(cands)}")
    return 0 if failed == 0 else 1


def _index_pages(get: Any, lo: str, hi: str) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    cursor = hi
    while cursor >= lo:
        out = (
            get(
                INDEX,
                "FHKUP03500100",
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": KOSDAQ_INDEX,
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
        rows = _index_pages(kis._get, "20090101", END)
    finally:
        kis.close()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "kosdaq.csv").write_text(
        "date,close\n" + "".join(f"{d},{c:g}\n" for d, c in rows), encoding="utf-8"
    )
    print(f"코스닥 지수 {len(rows)} 일 ({rows[0][0]} ~ {rows[-1][0]})")
    return 0


def fetch_flows(shard: int = 0, shards: int = 1) -> int:
    FLOWS.mkdir(parents=True, exist_ok=True)
    cands = _cands()
    todo = [c for i, c in enumerate(sorted(cands)) if i % shards == shard]
    kis = _kis()
    done = failed = 0
    try:
        for k, code in enumerate(todo, 1):
            path = FLOWS / f"{code}.csv"
            if path.exists():
                continue
            c = cands[code]
            lo, hi = max(START, _shift(c["first"], FLOW_LEAD)), min(c["last"], END)
            try:
                rows = _flow_pages(kis._get, code, lo, hi)
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("universe", "info", "prices", "index", "flows"))
    parser.add_argument("--shard", default="0/1", help="flows 나눠 받기: i/n")
    args = parser.parse_args(argv)
    if args.stage == "flows":
        i, n = (int(x) for x in args.shard.split("/"))
        return fetch_flows(i, n)
    stages = {
        "universe": build_universe,
        "info": fetch_info,
        "prices": fetch_prices,
        "index": fetch_index,
    }
    return stages[args.stage]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
