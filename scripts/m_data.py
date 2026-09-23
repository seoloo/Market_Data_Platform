"""M 다중 종목 캐리 — 시점별 유니버스 계산과 필요한 데이터만 적재 (13 사전등록).

유니버스 (매월 1일 00:00 UTC, 그 시점까지의 정보만 사용):

1. Binance USD-M ``*USDT`` 무기한 중 1000배수 계약(숫자로 시작)과 스테이블코인 기초자산 제외
2. 선물 1일봉 첫 봉이 90일 이전 (상장 90일 이상)
3. 직전 30일 선물 거래대금 합 상위 **30** → 후보
4. 후보 중 같은 이름의 **현물 페어가 직전 24시간 안에 거래**된 종목만, 순위대로 **20**개 → 유니버스

폐지 종목이 들어 있는 1일봉 전체에서 고르므로 생존 편향이 없다. 수익·가격 수준은 쓰지 않는다.

사용 예::

    python scripts/m_data.py daily      # 0단계: 2022년 이전 상장 종목의 선물 1d (2019-09 ~ 2022-12)
    python scripts/m_data.py spot       # 1단계: 후보의 현물 1h (해당 달과 전달)
    python scripts/m_data.py universe   # 2단계: 유니버스 계산 → reports/m/universe.json
    python scripts/m_data.py perp       # 3단계: 유니버스 종목의 선물 1h·funding (해당 달과 다음 달)
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from scripts.backfill_spot import parse_spot

from auto_trading.config.loader import load_config
from auto_trading.core.enums import Timeframe
from auto_trading.marketdata.archive import (
    ArchiveFile,
    BinanceVisionClient,
    FundingArchiveFile,
    Granularity,
    parse_funding_csv,
)
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.candles import CandleRepository
from auto_trading.storage.repositories.funding import FundingRateRepository

DB = Path("data/trading.db")
UNIVERSE = Path("reports/m/universe.json")
COVERAGE = Path("reports/phase4/symbol_coverage.json")
"""상장폐지 포함 전체 USD-M 심볼의 첫·마지막 달 (build_universe.py가 만든 목록)."""
FIRST_MONTH = (2020, 1)
LAST_MONTH = (2026, 8)
CANDIDATES = 30
UNIVERSE_SIZE = 20
MIN_AGE_DAYS = 90
VOLUME_DAYS = 30
STABLE = frozenset(
    {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "EUR", "GBP", "AEUR", "USDE", "PYUSD"}
    | {"RLUSD", "USD1", "BFUSD", "UST", "USTC", "SUSD", "PAX"}
)
_DAY = 86_400_000
_HOUR = 3_600_000


def month_starts() -> list[int]:
    out = []
    year, month = FIRST_MONTH
    while (year, month) <= LAST_MONTH:
        out.append(int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _month_date(ms: int) -> date:
    moment = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return date(moment.year, moment.month, 1)


def _shift(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def eligible_symbol(symbol: str) -> bool:
    return symbol.endswith("USDT") and not symbol[0].isdigit() and symbol[:-4] not in STABLE


def candidates(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """월 시작 → 직전 30일 선물 거래대금 상위 30 (상장 90일 이상)."""
    first: dict[str, int] = {}
    volume: dict[str, dict[int, float]] = defaultdict(dict)
    for symbol, ms, quote in conn.execute(
        "SELECT symbol, open_ms, quote_volume FROM candles"
        " WHERE exchange = 'binance_usdm' AND timeframe = '1d'"
    ):
        first[symbol] = min(first.get(symbol, ms), ms)
        volume[symbol][ms] = float(quote or 0)
    out: dict[int, list[str]] = {}
    for start in month_starts():
        ranked = []
        for symbol, days in volume.items():
            if not eligible_symbol(symbol) or first[symbol] > start - MIN_AGE_DAYS * _DAY:
                continue
            total = sum(days.get(start - k * _DAY, 0.0) for k in range(1, VOLUME_DAYS + 1))
            if total > 0:
                ranked.append((total, symbol))
        ranked.sort(reverse=True)
        out[start] = [symbol for _, symbol in ranked[:CANDIDATES]]
    return out


def universe(conn: sqlite3.Connection, cands: dict[int, list[str]]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for start, symbols in cands.items():
        chosen = []
        for symbol in symbols:
            row = conn.execute(
                "SELECT 1 FROM candles WHERE exchange = 'binance_spot' AND symbol = ?"
                " AND timeframe = '1h' AND open_ms >= ? AND open_ms < ? LIMIT 1",
                (symbol, start - _DAY, start),
            ).fetchone()
            if row is not None:
                chosen.append(symbol)
            if len(chosen) == UNIVERSE_SIZE:
                break
        out[start] = chosen
    return out


def _fetch(job: tuple[str, str, date]) -> tuple[tuple[str, str, date], object]:
    kind, symbol, month = job
    with BinanceVisionClient(request_delay_s=0.05) as client:
        if kind == "funding":
            payload = client.download(FundingArchiveFile(symbol, month))
            return job, None if payload is None else parse_funding_csv(_unzip(payload))
        market = "spot" if kind == "spot" else "futures/um"
        frame = Timeframe.D1 if kind == "daily" else Timeframe.H1
        file = ArchiveFile(symbol, frame, month, Granularity.MONTHLY, market=market)
        payload = client.download(file)
        return job, None if payload is None else parse_spot(payload, frame)


def _unzip(payload: bytes) -> bytes:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        (name,) = archive.namelist()
        return archive.read(name)


def _existing(conn: sqlite3.Connection, kind: str) -> set[tuple[str, date]]:
    if kind == "funding":
        rows = conn.execute("SELECT symbol, funding_ms FROM funding_rates").fetchall()
    elif kind == "daily":
        rows = conn.execute(
            "SELECT symbol, open_ms FROM candles"
            " WHERE exchange = 'binance_usdm' AND timeframe = '1d'"
        ).fetchall()
    else:
        exchange = "binance_spot" if kind == "spot" else "binance_usdm"
        rows = conn.execute(
            "SELECT symbol, open_ms FROM candles WHERE exchange = ? AND timeframe = '1h'",
            (exchange,),
        ).fetchall()
    return {(symbol, _month_date(int(ms))) for symbol, ms in rows}


def load_jobs(jobs: list[tuple[str, str, date]]) -> None:
    config = load_config().config
    with Database(config.data.db_path) as db:
        done = {k: _existing(db.connection, k) for k in {job[0] for job in jobs}}
        todo = [job for job in jobs if (job[1], job[2]) not in done[job[0]]]
        print(f"작업 {len(jobs)}개 중 {len(todo)}개 적재", flush=True)
        spot = CandleRepository(db.connection, exchange="binance_spot")
        perp = CandleRepository(db.connection, exchange="binance_usdm")
        funding = FundingRateRepository(db.connection)
        missing = skipped_total = 0
        with futures.ThreadPoolExecutor(max_workers=8) as pool:
            for n, (job, data) in enumerate(pool.map(_fetch, todo), start=1):
                kind, symbol, _ = job
                now = int(time.time() * 1000)
                if data is None:
                    missing += 1
                elif kind == "funding":
                    funding.upsert_many(symbol, data)  # type: ignore[arg-type]
                else:
                    candles, skipped = data  # type: ignore[misc]
                    skipped_total += skipped
                    repo = spot if kind == "spot" else perp
                    frame = Timeframe.D1 if kind == "daily" else Timeframe.H1
                    repo.upsert_many(symbol, frame, candles, source="bulk", ingested_ms=now)
                if n % 200 == 0:
                    db.connection.commit()
                    print(f"  {n}/{len(todo)} (없음 {missing})", flush=True)
            db.connection.commit()
        print(f"완료. 파일 없음 {missing}, 잘린 봉 제외 {skipped_total}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("daily", "spot", "universe", "perp"))
    args = parser.parse_args()
    if args.stage == "daily":
        coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
        jobs = []
        for symbol, info in coverage.items():
            if info["first"] > "2022-12":
                continue
            first = date(*map(int, info["first"].split("-")), 1)
            month = max(first, date(2019, 9, 1))
            while month <= date(2022, 12, 1):
                jobs.append(("daily", symbol, month))
                month = _shift(month, 1)
        load_jobs(jobs)
        return 0
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        cands = candidates(conn)
    if args.stage == "spot":
        jobs = {
            ("spot", symbol, _shift(_month_date(start), shift))
            for start, symbols in cands.items()
            for symbol in symbols
            for shift in (-1, 0)
        }
        load_jobs(sorted(jobs))
        return 0
    if args.stage == "universe":
        with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
            chosen = universe(conn, cands)
        body = {str(k): v for k, v in chosen.items()}
        text = json.dumps(body, ensure_ascii=True, indent=1, sort_keys=True)
        UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
        UNIVERSE.write_text(text + "\n", encoding="utf-8", newline="\n")
        sizes = [len(v) for v in chosen.values()]
        symbols = {s for v in chosen.values() for s in v}
        digest = hashlib.sha256(text.encode()).hexdigest()
        print(f"월 {len(sizes)}개, 크기 {min(sizes)}~{max(sizes)}, 종목 {len(symbols)}개, {digest}")
        return 0
    chosen = {int(k): v for k, v in json.loads(UNIVERSE.read_text(encoding="utf-8")).items()}
    jobs = {
        (kind, symbol, _shift(_month_date(start), shift))
        for start, symbols in chosen.items()
        for symbol in symbols
        for shift in (0, 1)
        for kind in ("perp", "funding", "spot")
    }
    jobs |= {
        ("funding", symbol, _shift(_month_date(start), -1))
        for start, symbols in chosen.items()
        for symbol in symbols
    }
    last = date(*LAST_MONTH, 1)
    load_jobs(sorted(job for job in jobs if job[2] <= last))
    return 0


if __name__ == "__main__":
    sys.exit(main())
