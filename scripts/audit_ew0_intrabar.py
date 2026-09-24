"""EW-0 4h/1m 전역 고저 순서 가용성 감사. DB 읽기 전용, 파일 출력 없음.

고정 범위: 2020-01-01 ~ 2021-12-31 UTC, Binance USD-M BTCUSDT.
워뇨띠 거래·손익을 읽지 않고 파동/피벗 라벨도 생성하지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.intrabar import audit_intrabar_extrema  # noqa: E402
from auto_trading.research.elliott.pivots import detect_guarded_pivots  # noqa: E402

FROM_MS = 1_577_836_800_000
TO_MS = 1_640_995_200_000
DB_PATH = REPO_ROOT / "data" / "trading.db"


def _candle(row: tuple[object, ...]) -> Candle:
    from decimal import Decimal

    return Candle(
        open_ms=int(str(row[0])),
        close_ms=int(str(row[1])),
        open=Decimal(str(row[2])),
        high=Decimal(str(row[3])),
        low=Decimal(str(row[4])),
        close=Decimal(str(row[5])),
        volume=Decimal(str(row[6])),
    )


def main() -> int:
    if not DB_PATH.is_file():
        raise FileNotFoundError(DB_PATH)
    connection = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        query = (
            "SELECT open_ms,close_ms,open,high,low,close,volume FROM candles "
            "WHERE exchange=? AND symbol=? AND timeframe=? AND open_ms>=? AND open_ms<? "
            "ORDER BY open_ms"
        )
        args = ("binance_usdm", "BTCUSDT", "4h", FROM_MS, TO_MS)
        four_hour = tuple(_candle(row) for row in connection.execute(query, args))
        if not four_hour:
            raise ValueError("4h 시장자료가 없다")
        guarded = detect_guarded_pivots(four_hour, four_hour[-1].close_ms)
        ambiguous = set(guarded.ambiguous_open_ms)

        minute_rows = iter(
            connection.execute(
                query,
                ("binance_usdm", "BTCUSDT", "1m", FROM_MS, TO_MS),
            )
        )
        minute_row = next(minute_rows, None)
        all_counts: Counter[str] = Counter()
        ambiguous_counts: Counter[str] = Counter()
        for outer in four_hour:
            while minute_row is not None and int(minute_row[0]) < outer.open_ms:
                minute_row = next(minute_rows, None)
            minute_candles: list[Candle] = []
            while minute_row is not None and int(minute_row[0]) < outer.open_ms + Timeframe.H4.ms:
                minute_candles.append(_candle(minute_row))
                minute_row = next(minute_rows, None)
            audit = audit_intrabar_extrema(outer, minute_candles, outer.close_ms)
            key = audit.reason.value if audit.reason is not None else audit.order.value
            all_counts[key] += 1
            if outer.open_ms in ambiguous:
                ambiguous_counts[key] += 1
    finally:
        connection.close()

    result = {
        "period_utc": "2020-01-01/2022-01-01",
        "source": "binance_usdm:BTCUSDT",
        "four_hour_bars": len(four_hour),
        "guarded_ambiguous_bars": len(ambiguous),
        "extremum_order_all": dict(sorted(all_counts.items())),
        "extremum_order_guarded_ambiguous": dict(sorted(ambiguous_counts.items())),
        "note": "전역 고저 순서 감사만 수행. ZigZag 경로/파동/WA 거래 판정 아님.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
