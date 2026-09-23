"""TA/v1 다종목 선물 추세 — 시점별 유니버스 계산과 funding 적재 (docs/research/20).

유니버스 (매월 1일 00:00 UTC, 그 시점까지의 정보만):
USD-M ``*USDT`` 무기한 중 1000배수 계약·스테이블코인·비암호 기초(금·주식 등) 제외, 상장 90일 이상,
직전 30일 선물 거래대금 상위 **20**. 현물 조건 없음 (M과 다르다). 폐지 종목 포함 목록에서 고른다.

사용 예::

    python scripts/ta_data.py universe   # reports/ta/universe.json + sha256
    python scripts/ta_data.py funding    # 유니버스 종목의 해당 달 funding 적재
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from scripts.build_universe import NON_CRYPTO_PREFIXES
from scripts.m_data import DB, _month_date, candidates, load_jobs

UNIVERSE = Path("reports/ta/universe.json")
SIZE = 20


def universe(conn: sqlite3.Connection) -> dict[int, list[str]]:
    return {
        start: [s for s in symbols if not s.startswith(NON_CRYPTO_PREFIXES)][:SIZE]
        for start, symbols in candidates(conn).items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("universe", "funding"))
    args = parser.parse_args()
    if args.stage == "universe":
        with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
            chosen = universe(conn)
        text = json.dumps({str(k): v for k, v in chosen.items()}, indent=1, sort_keys=True)
        UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
        UNIVERSE.write_text(text + "\n", encoding="utf-8", newline="\n")
        sizes = [len(v) for v in chosen.values()]
        full = [k for k, v in sorted(chosen.items()) if len(v) == SIZE]
        print(
            f"월 {len(sizes)}개, 크기 {min(sizes)}~{max(sizes)}, 20개 첫 달 {_month_date(full[0])},"
            f" 종목 {len({s for v in chosen.values() for s in v})}개,"
            f" sha256 {hashlib.sha256(text.encode()).hexdigest()}"
        )
        return 0
    chosen = {int(k): v for k, v in json.loads(UNIVERSE.read_text(encoding="utf-8")).items()}
    jobs = sorted(
        {("funding", s, _month_date(start)) for start, symbols in chosen.items() for s in symbols}
    )
    load_jobs(jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
