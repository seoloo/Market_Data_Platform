"""EW-0 1m 경로의 시장자료 가용성 감사. WA/성과 자료 미조회, DB 읽기 전용.

1m은 완성 4h 내부 순서 확인에만 쓴다. Elliott 파동 라벨은 만들지 않으며
실거래·Shadow 판단 입력으로도 연결하지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.minute_path import (  # noqa: E402
    MinutePathReplay,
    replay_minute_groups,
)

FROM_MS = 1_577_836_800_000
TO_MS = 1_640_995_200_000
DB_PATH = REPO_ROOT / "data" / "trading.db"
_QUERY = (
    "SELECT open_ms,close_ms,open,high,low,close,volume FROM candles "
    "WHERE exchange=? AND symbol=? AND timeframe=? AND open_ms>=? AND open_ms<? "
    "ORDER BY open_ms"
)


def _candle(row: tuple[object, ...]) -> Candle:
    return Candle(
        open_ms=int(str(row[0])),
        close_ms=int(str(row[1])),
        open=Decimal(str(row[2])),
        high=Decimal(str(row[3])),
        low=Decimal(str(row[4])),
        close=Decimal(str(row[5])),
        volume=Decimal(str(row[6])),
    )


def _minute_groups(
    connection: sqlite3.Connection, four_hour: tuple[Candle, ...]
) -> Iterator[list[Candle]]:
    rows = iter(connection.execute(_QUERY, ("binance_usdm", "BTCUSDT", "1m", FROM_MS, TO_MS)))
    row = next(rows, None)
    for outer in four_hour:
        while row is not None and int(row[0]) < outer.open_ms:
            row = next(rows, None)
        group: list[Candle] = []
        while row is not None and int(row[0]) < outer.open_ms + Timeframe.H4.ms:
            group.append(_candle(row))
            row = next(rows, None)
        yield group


def load_market_replay(db_path: Path) -> tuple[tuple[Candle, ...], MinutePathReplay]:
    """시장 봉만 읽어 시간순 경로를 재생한다. DB·보고서 파일은 쓰지 않는다."""
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        four_hour = tuple(
            _candle(row)
            for row in connection.execute(_QUERY, ("binance_usdm", "BTCUSDT", "4h", FROM_MS, TO_MS))
        )
        if not four_hour:
            raise ValueError("4h 시장자료가 없다")
        replay = replay_minute_groups(
            four_hour, _minute_groups(connection, four_hour), four_hour[-1].close_ms
        )
    finally:
        connection.close()
    return four_hour, replay


def main() -> int:
    four_hour, replay = load_market_replay(DB_PATH)

    events = [event for segment in replay.segments for event in segment]
    blocked = Counter(item.reason.value for item in replay.blocked_four_hours)
    release_delays_min = sorted(
        (event.available_at_ms - event.confirmation_minute_close_ms) // Timeframe.M1.ms
        for event in events
    )
    result = {
        "period_utc": "2020-01-01/2022-01-01",
        "source": "binance_usdm:BTCUSDT",
        "four_hour_bars": len(four_hour),
        "quality_accepted_four_hour_bars": len(four_hour) - len(replay.blocked_four_hours),
        "blocked_four_hour_reasons": dict(sorted(blocked.items())),
        "ambiguous_minute_bars": len(replay.ambiguous_minute_open_ms),
        "path_segments": len(replay.segments),
        "segments_with_3_or_more_events": sum(len(segment) >= 3 for segment in replay.segments),
        "segments_with_6_or_more_events": sum(len(segment) >= 6 for segment in replay.segments),
        "confirmed_path_events": len(events),
        "distinct_four_hours_with_events": len({event.available_at_ms for event in events}),
        "release_delay_min_median": median(release_delays_min) if release_delays_min else None,
        "release_delay_min_max": max(release_delays_min) if release_delays_min else None,
        "note": "1m 경로·품질 coverage만. 4h Elliott 라벨/WA 거래/성과 검정 아님.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
