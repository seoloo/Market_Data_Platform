"""KRX Open API — 유가증권·코스닥 전 종목 일별 매매(시가총액·상장주식수)·코스닥 종목 기본정보 받기.

사용 예::

    python -m scripts.fetch_krx daily     # 2015-01 ~ 2026-08 거래일마다 유가증권·코스닥
    python -m scripts.fetch_krx kosdaq_info   # 코스닥 종목 기본정보 (지금 상장 종목)
    python -m scripts.fetch_krx status

* 쓰는 곳: 실적 연구의 KOSPI 총수익 대리 지수(시가총액 · 배당, FUNDAMENTAL_KR_GATES §6),
  코스닥 시험(IDEAS 1·14)의 그 시점 시가총액 종목군.
* 키는 ``.env`` 의 ``KRX_API_KEY`` 를 ``AUTH_KEY`` 머리글로만 보낸다(출력·파일에 남기지 않음).
* 저장: ``data/research/krx/{stk,ksq}/YYYYMMDD.csv.gz`` (git 제외). 이미 받은 날은 건너뛴다.
  KRX 가 그날 0 행을 주면(휴장) 빈 파일 대신 ``.empty`` 표시를 남긴다.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "data" / "research" / "krx"
API = "https://data-dbg.krx.co.kr/svc/apis"
MARKETS = {"stk": "sto/stk_bydd_trd", "ksq": "sto/ksq_bydd_trd"}
FIELDS = (
    "ISU_CD",
    "ISU_NM",
    "MKT_NM",
    "SECT_TP_NM",
    "TDD_OPNPRC",
    "TDD_HGPRC",
    "TDD_LWPRC",
    "TDD_CLSPRC",
    "ACC_TRDVOL",
    "ACC_TRDVAL",
    "MKTCAP",
    "LIST_SHRS",
)
START, END = "20150101", "20260831"
PAUSE = 0.05


def to_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """응답 행 → 고른 칸만 담은 CSV (순수). 숫자의 쉼표는 뺀다."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(FIELDS)
    for r in rows:
        w.writerow([str(r.get(f, "")).replace(",", "") for f in FIELDS])
    return buf.getvalue().encode("utf-8")


def trading_days() -> list[str]:
    """KOSPI 지수 자료의 거래일 (YYYYMMDD) — 141 자료와 같은 달력."""
    rows = (REPO / "data" / "research" / "kr_stocks" / "kospi.csv").read_text().splitlines()[1:]
    return [r.split(",")[0] for r in rows if START <= r.split(",")[0] <= END]


def _key() -> str:
    from auto_trading.config.secrets import read_env_file

    key = read_env_file(REPO / ".env").get("KRX_API_KEY", "")
    if not key:
        raise SystemExit("KRX_API_KEY 가 .env 에 없다")
    return key


def fetch_daily() -> dict[str, Any]:
    import httpx

    key = _key()
    got = skipped = empty = failed = 0
    with httpx.Client(timeout=60, headers={"AUTH_KEY": key}) as client:
        for day in trading_days():
            for mk, ep in MARKETS.items():
                path = ROOT / mk / f"{day}.csv.gz"
                mark = path.with_suffix(".empty")
                if path.exists() or mark.exists():
                    skipped += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    resp = client.get(f"{API}/{ep}", params={"basDd": day})
                    resp.raise_for_status()
                    rows = resp.json().get("OutBlock_1") or []
                except Exception as exc:  # 하루 실패가 전체를 멈추지 않게 — 다음 실행이 채운다
                    failed += 1
                    print(f"{mk} {day} 실패: {type(exc).__name__}", flush=True)
                    time.sleep(2)
                    continue
                if not rows:
                    mark.write_text("", encoding="utf-8")
                    empty += 1
                else:
                    path.write_bytes(gzip.compress(to_csv(rows)))
                    got += 1
                time.sleep(PAUSE)
            if got and got % 500 == 0:
                print(f"{day} 받음 {got}", flush=True)
    return {"got": got, "skipped": skipped, "empty": empty, "failed": failed}


def fetch_kosdaq_info() -> dict[str, Any]:
    import httpx

    with httpx.Client(timeout=60, headers={"AUTH_KEY": _key()}) as client:
        resp = client.get(f"{API}/sto/ksq_isu_base_info", params={"basDd": END})
        resp.raise_for_status()
        rows = resp.json().get("OutBlock_1") or []
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "ksq_info.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return {"rows": len(rows)}


def status() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mk in MARKETS:
        folder = ROOT / mk
        out[mk] = {
            "files": len(list(folder.glob("*.csv.gz"))) if folder.exists() else 0,
            "empty": len(list(folder.glob("*.empty"))) if folder.exists() else 0,
        }
    out["days"] = len(trading_days())
    return out


def main(argv: list[str]) -> int:
    stages = {"daily": fetch_daily, "kosdaq_info": fetch_kosdaq_info, "status": status}
    if len(argv) != 1 or argv[0] not in stages:
        raise SystemExit("usage: python -m scripts.fetch_krx daily|kosdaq_info|status")
    sys.stdout.write(json.dumps(stages[argv[0]](), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
