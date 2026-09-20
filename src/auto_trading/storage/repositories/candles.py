"""캔들 저장소.

금액·수량은 ``TEXT``(Decimal 문자열)로 저장한다. ``float`` 를 거치면 정밀도가 깨진다
(09-failure-modes.md F10).

읽을 때는 :class:`~auto_trading.core.types.Candle` 로 복원하며, 그 과정에서
OHLC 관계 검증이 자동으로 수행된다 — 잘못된 데이터가 DB 에서 나와도 타입 경계에서 막힌다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle, TimeframeSeries

__all__ = ["CandleGap", "CandleRepository"]


@dataclass(frozen=True, slots=True)
class CandleGap:
    """연속되어야 할 봉 사이의 결측 구간."""

    timeframe: Timeframe
    missing_from_ms: int
    missing_to_ms: int
    bar_count: int


def _row_to_candle(row: sqlite3.Row) -> Candle:
    return Candle(
        open_ms=int(row["open_ms"]),
        close_ms=int(row["close_ms"]),
        open=Decimal(row["open"]),
        high=Decimal(row["high"]),
        low=Decimal(row["low"]),
        close=Decimal(row["close"]),
        volume=Decimal(row["volume"]),
        quote_volume=Decimal(row["quote_volume"]),
        trade_count=int(row["trade_count"]),
    )


class CandleRepository:
    """캔들 읽기/쓰기. 저장소 접근은 전부 이 계층 뒤에 숨긴다."""

    __slots__ = ("_conn", "exchange")

    def __init__(self, conn: sqlite3.Connection, *, exchange: str = "binance_usdm") -> None:
        self._conn = conn
        self.exchange = exchange

    # ------------------------------------------------------------- 쓰기

    def upsert_many(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: Iterable[Candle],
        *,
        source: str,
        ingested_ms: int,
    ) -> int:
        """캔들을 삽입하거나 갱신한다.

        같은 봉을 여러 소스(bulk / rest / ws)에서 받을 수 있으므로 멱등해야 한다.
        ``source`` 는 나중에 데이터 출처를 추적하기 위해 덮어쓴다.

        Returns:
            처리한 봉 수.
        """
        rows = [
            (
                self.exchange,
                symbol,
                timeframe.value,
                c.open_ms,
                c.close_ms,
                str(c.open),
                str(c.high),
                str(c.low),
                str(c.close),
                str(c.volume),
                str(c.quote_volume),
                c.trade_count,
                source,
                ingested_ms,
            )
            for c in candles
        ]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT INTO candles (exchange, symbol, timeframe, open_ms, close_ms,"
            " open, high, low, close, volume, quote_volume, trade_count, source, ingested_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (exchange, symbol, timeframe, open_ms) DO UPDATE SET"
            "   close_ms = excluded.close_ms,"
            "   open = excluded.open, high = excluded.high,"
            "   low = excluded.low, close = excluded.close,"
            "   volume = excluded.volume, quote_volume = excluded.quote_volume,"
            "   trade_count = excluded.trade_count,"
            "   source = excluded.source, ingested_ms = excluded.ingested_ms",
            rows,
        )
        return len(rows)

    # ------------------------------------------------------------- 읽기

    def get_range(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int | None = None,
    ) -> tuple[Candle, ...]:
        """구간 조회 (open_ms 오름차순).

        ``limit`` 을 주면 **최근** N개를 가져와 다시 오름차순으로 돌려준다.
        """
        clauses = ["exchange = ?", "symbol = ?", "timeframe = ?"]
        params: list[object] = [self.exchange, symbol, timeframe.value]
        if from_ms is not None:
            clauses.append("open_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            clauses.append("open_ms <= ?")
            params.append(to_ms)
        where = " AND ".join(clauses)

        if limit is None:
            sql = f"SELECT * FROM candles WHERE {where} ORDER BY open_ms ASC"
        else:
            sql = f"SELECT * FROM candles WHERE {where} ORDER BY open_ms DESC LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        if limit is not None:
            rows = list(reversed(rows))
        return tuple(_row_to_candle(r) for r in rows)

    def get_closed_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        as_of_ms: int,
        *,
        limit: int,
    ) -> TimeframeSeries:
        """``as_of_ms`` 시점에 **마감된** 봉만 담은 시계열.

        형성 중인 봉을 제외하는 것이 look-ahead 차단의 기본이다 (AGENTS.md §3.2).
        ``close_ms <= as_of_ms`` 조건이 그 역할을 한다.
        """
        rows = self._conn.execute(
            "SELECT * FROM candles"
            " WHERE exchange = ? AND symbol = ? AND timeframe = ? AND close_ms <= ?"
            " ORDER BY open_ms DESC LIMIT ?",
            (self.exchange, symbol, timeframe.value, as_of_ms, limit),
        ).fetchall()
        candles = tuple(_row_to_candle(r) for r in reversed(rows))
        return TimeframeSeries(timeframe=timeframe, candles=candles)

    def last_open_ms(self, symbol: str, timeframe: Timeframe) -> int | None:
        """마지막으로 저장된 봉의 시작 시각. 백필 재개 지점을 찾는 데 쓴다."""
        row = self._conn.execute(
            "SELECT MAX(open_ms) AS m FROM candles"
            " WHERE exchange = ? AND symbol = ? AND timeframe = ?",
            (self.exchange, symbol, timeframe.value),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def first_open_ms(self, symbol: str, timeframe: Timeframe) -> int | None:
        row = self._conn.execute(
            "SELECT MIN(open_ms) AS m FROM candles"
            " WHERE exchange = ? AND symbol = ? AND timeframe = ?",
            (self.exchange, symbol, timeframe.value),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def count(self, symbol: str, timeframe: Timeframe) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM candles WHERE exchange = ? AND symbol = ? AND timeframe = ?",
            (self.exchange, symbol, timeframe.value),
        ).fetchone()
        return 0 if row is None else int(row["c"])

    # ------------------------------------------------------------- 무결성

    def find_gaps(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> tuple[CandleGap, ...]:
        """결측 구간을 찾는다.

        SQL 로 직접 인접 봉 간격을 비교한다. 전 구간을 파이썬으로 끌어오면
        수백만 행에서 메모리를 낭비한다.
        """
        clauses = ["exchange = ?", "symbol = ?", "timeframe = ?"]
        params: list[object] = [self.exchange, symbol, timeframe.value]
        if from_ms is not None:
            clauses.append("open_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            clauses.append("open_ms <= ?")
            params.append(to_ms)
        where = " AND ".join(clauses)

        sql = (
            "WITH ordered AS ("
            f"  SELECT open_ms, LEAD(open_ms) OVER (ORDER BY open_ms) AS next_ms"
            f"  FROM candles WHERE {where}"
            ") SELECT open_ms, next_ms FROM ordered"
            " WHERE next_ms IS NOT NULL AND next_ms - open_ms > ?"
        )
        params.append(timeframe.ms)
        rows = self._conn.execute(sql, params).fetchall()

        step = timeframe.ms
        return tuple(
            CandleGap(
                timeframe=timeframe,
                missing_from_ms=int(r["open_ms"]) + step,
                missing_to_ms=int(r["next_ms"]) - step,
                bar_count=(int(r["next_ms"]) - int(r["open_ms"])) // step - 1,
            )
            for r in rows
        )

    def record_gaps(self, symbol: str, gaps: Sequence[CandleGap], *, detected_ms: int) -> int:
        """발견한 갭을 기록한다. 같은 갭을 다시 넣어도 중복되지 않는다."""
        if not gaps:
            return 0
        self._conn.executemany(
            "INSERT INTO candle_gaps"
            " (exchange, symbol, timeframe, missing_from_ms, missing_to_ms, bar_count,"
            "  resolved, detected_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, ?)"
            " ON CONFLICT (exchange, symbol, timeframe, missing_from_ms) DO UPDATE SET"
            "   missing_to_ms = excluded.missing_to_ms,"
            "   bar_count = excluded.bar_count",
            [
                (
                    self.exchange,
                    symbol,
                    g.timeframe.value,
                    g.missing_from_ms,
                    g.missing_to_ms,
                    g.bar_count,
                    detected_ms,
                )
                for g in gaps
            ],
        )
        return len(gaps)

    def resolve_gaps(self, symbol: str, timeframe: Timeframe, *, resolved_ms: int) -> int:
        """이제 데이터가 채워진 갭을 해소 처리한다."""
        cursor = self._conn.execute(
            "UPDATE candle_gaps SET resolved = 1, resolved_ms = ?"
            " WHERE exchange = ? AND symbol = ? AND timeframe = ? AND resolved = 0"
            " AND ("
            "   SELECT COUNT(*) FROM candles c"
            "   WHERE c.exchange = candle_gaps.exchange AND c.symbol = candle_gaps.symbol"
            "     AND c.timeframe = candle_gaps.timeframe"
            "     AND c.open_ms BETWEEN candle_gaps.missing_from_ms AND candle_gaps.missing_to_ms"
            " ) >= candle_gaps.bar_count",
            (resolved_ms, self.exchange, symbol, timeframe.value),
        )
        return cursor.rowcount

    def open_gap_count(self, symbol: str, timeframe: Timeframe | None = None) -> int:
        """미해결 갭 수. 0 이 아니면 진입을 거부한다 (게이트 G2.3)."""
        if timeframe is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM candle_gaps"
                " WHERE exchange = ? AND symbol = ? AND resolved = 0",
                (self.exchange, symbol),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM candle_gaps"
                " WHERE exchange = ? AND symbol = ? AND timeframe = ? AND resolved = 0",
                (self.exchange, symbol, timeframe.value),
            ).fetchone()
        return 0 if row is None else int(row["c"])
