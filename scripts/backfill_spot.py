#!/usr/bin/env python
"""Binance **현물** BTCUSDT 캔들 백필 (K 펀딩 캐리 연구의 베이시스용, 10 사전등록).

data.binance.vision 현물 월별 아카이브를 체크섬 검증 후 ``exchange='binance_spot'`` 으로 적재한다.
선물 캔들(``binance_usdm``)과 섞이지 않는다. 실거래 경로는 이 데이터를 읽지 않는다.

사용 예::

    python scripts/backfill_spot.py --start 2020-01-01 --end 2026-08-31
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.marketdata.archive import (  # noqa: E402
    ArchiveFile,
    BinanceVisionClient,
    Granularity,
    months_between,
)
from auto_trading.storage.db import Database  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402

EXCHANGE = "binance_spot"
_US_BOUND = 4_102_444_800_000
"""이보다 큰 타임스탬프는 마이크로초 (현물 아카이브는 2025년부터 μs)."""


def _ms(raw: str) -> int:
    value = int(raw)
    return value // 1000 if value > _US_BOUND else value


def parse_spot(payload: bytes, timeframe: Timeframe) -> tuple[list[Candle], int]:
    """현물 klines CSV. 거래소 점검으로 **잘린 봉은 건너뛰고** 그 수를 돌려준다.

    선물 파서(`parse_klines_csv`)는 봉 길이가 다르면 실패한다. 현물 아카이브에는 점검 구간의
    잘린 봉이 있으므로, 잘린 봉을 버리고 결측으로 남긴다(보간하지 않는다).
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        (name,) = archive.namelist()
        text = archive.read(name).decode("utf-8")
    candles: list[Candle] = []
    skipped = 0
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():
            continue
        open_ms, close_ms = _ms(row[0]), _ms(row[6])
        if open_ms % timeframe.ms or close_ms - open_ms != timeframe.ms - 1:
            skipped += 1
            continue
        candles.append(
            Candle(
                open_ms=open_ms,
                close_ms=close_ms,
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[5]),
                quote_volume=Decimal(row[7]),
                trade_count=int(row[8]),
            )
        )
    return candles, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 31))
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    timeframe = Timeframe(args.timeframe)
    config = load_config().config
    total = 0
    with BinanceVisionClient(request_delay_s=0.1) as client, Database(config.data.db_path) as db:
        repo = CandleRepository(db.connection, exchange=EXCHANGE)
        for month in months_between(args.start, args.end):
            file = ArchiveFile(args.symbol, timeframe, month, Granularity.MONTHLY, market="spot")
            payload = client.download(file)
            if payload is None:
                print(f"{file}: 없음")
                continue
            candles, skipped = parse_spot(payload, timeframe)
            count = repo.upsert_many(
                args.symbol,
                timeframe,
                candles,
                source="bulk",
                ingested_ms=int(time.time() * 1000),
            )
            db.connection.commit()
            total += count
            print(
                f"{file}: {count}" + (f" (잘린 봉 {skipped} 제외)" if skipped else ""), flush=True
            )
    print(f"합계 {total:,}봉 ({EXCHANGE} {args.symbol} {timeframe.value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
