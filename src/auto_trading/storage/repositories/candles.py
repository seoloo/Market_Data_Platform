"""캔들(봉) 저장소 — ``candles`` · ``candle_gaps`` 표 읽기 · 쓰기.

언제 쓰나: 백필(marketdata/backfill)이 봉을 넣고, 무결성 검사 · 스냅샷 · 백테스트가 봉을 읽을 때.
읽고 쓰는 것: ``candles`` (봉), ``candle_gaps`` (발견한 빈 구간 기록).

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
    """연속되어야 할 봉 사이의 결측 구간 (빠진 첫 봉 · 마지막 봉의 시작 시각과 개수)."""

    timeframe: Timeframe
    missing_from_ms: int
    missing_to_ms: int
    bar_count: int


def _row_to_candle(row: sqlite3.Row) -> Candle:
    """[쓰임] DB 한 줄을 :class:`Candle` 로 바꾼다 (TEXT 로 저장한 값은 Decimal 로 되돌린다).
    [이유] Candle 을 만들 때 고가 ≥ 저가 같은 관계를 검사하므로, DB 의 잘못된 값도 여기서 막힌다."""
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
    """캔들 읽기/쓰기 창구. 저장소 접근은 전부 이 계층 뒤에 숨긴다.

    ``exchange`` 는 같은 표에 여러 거래소 봉을 함께 두기 위한 구분 값이다 (기본 바이낸스 USD-M).
    """

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
        """[쓰임] 봉들을 넣고 처리한 봉 수를 돌려준다. 같은 봉(거래소 · 종목 · 타임프레임 · 시작
                  시각)이 이미 있으면 새 값으로 바꾼다.
        [이유] 같은 봉을 여러 출처(bulk / rest / ws)에서 받을 수 있으므로 몇 번 넣어도 결과가 같아야
               한다 (멱등). ``source`` · ``ingested_ms`` 는 '어디서 · 언제 받은 값인가' 를
               추적하려고 덮어쓴다."""
        rows = [
            (
                self.exchange,
                symbol,
                timeframe.value,
                candle.open_ms,
                candle.close_ms,
                str(candle.open),
                str(candle.high),
                str(candle.low),
                str(candle.close),
                str(candle.volume),
                str(candle.quote_volume),
                candle.trade_count,
                source,
                ingested_ms,
            )
            for candle in candles
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
        """[쓰임] 구간 ``[from_ms, to_ms]`` (봉 시작 시각 기준) 의 봉을 시간 순서로 돌려준다.
        ``limit`` 을 주면 **가장 최근** N 개를 가져와 다시 시간 순서로 돌려준다.
        [이유] 최근 N 개는 DB 에서 내림차순으로 잘라 와야 빠르다 — 그래서 뒤집어서 돌려준다."""
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
        return tuple(_row_to_candle(row) for row in rows)

    def get_closed_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        as_of_ms: int,
        *,
        limit: int,
    ) -> TimeframeSeries:
        """[쓰임] ``as_of_ms`` 시점에 **이미 마감된** 봉만 최근 ``limit`` 개 담아 시계열로 돌려준다.
        [이유] 아직 형성 중인 봉을 쓰면 그 시점에는 몰랐던 값(미래)을 보는 셈이다 (look-ahead,
               AGENTS.md §3.2). ``close_ms <= as_of_ms`` 조건이 그것을 막는다."""
        rows = self._conn.execute(
            "SELECT * FROM candles"
            " WHERE exchange = ? AND symbol = ? AND timeframe = ? AND close_ms <= ?"
            " ORDER BY open_ms DESC LIMIT ?",
            (self.exchange, symbol, timeframe.value, as_of_ms, limit),
        ).fetchall()
        candles = tuple(_row_to_candle(row) for row in reversed(rows))
        return TimeframeSeries(timeframe=timeframe, candles=candles)

    def last_open_ms(self, symbol: str, timeframe: Timeframe) -> int | None:
        """[쓰임] 마지막으로 저장된 봉의 시작 시각 (없으면 None).
        [이유] 백필을 이어 받을 지점을 찾는 데 쓴다."""
        row = self._conn.execute(
            "SELECT MAX(open_ms) AS m FROM candles"
            " WHERE exchange = ? AND symbol = ? AND timeframe = ?",
            (self.exchange, symbol, timeframe.value),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def first_open_ms(self, symbol: str, timeframe: Timeframe) -> int | None:
        """[쓰임] 처음 저장된 봉의 시작 시각 (없으면 None) — 무결성 검사의 시작점."""
        row = self._conn.execute(
            "SELECT MIN(open_ms) AS m FROM candles"
            " WHERE exchange = ? AND symbol = ? AND timeframe = ?",
            (self.exchange, symbol, timeframe.value),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def count(self, symbol: str, timeframe: Timeframe) -> int:
        """[쓰임] 저장된 봉 개수."""
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
        """[쓰임] 이웃한 두 봉 사이가 봉 하나 길이보다 벌어진 곳(빠진 봉)을 모두 찾는다.
        [이유] SQL 창 함수(``LEAD``)로 DB 안에서 바로 비교한다 — 수백만 행을 파이썬으로 끌어오면
               메모리를 낭비한다."""
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

        bar_ms = timeframe.ms
        return tuple(
            CandleGap(
                timeframe=timeframe,
                missing_from_ms=int(row["open_ms"]) + bar_ms,
                missing_to_ms=int(row["next_ms"]) - bar_ms,
                bar_count=(int(row["next_ms"]) - int(row["open_ms"])) // bar_ms - 1,
            )
            for row in rows
        )

    def record_gaps(self, symbol: str, gaps: Sequence[CandleGap], *, detected_ms: int) -> int:
        """[쓰임] 찾은 빈 구간을 ``candle_gaps`` 에 '미해결' 로 적는다 (같은 구간은 한 줄만).
        [이유] 빈 구간이 남아 있으면 진입을 막는 게이트(G2.3)가 이 표를 본다."""
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
                    gap.timeframe.value,
                    gap.missing_from_ms,
                    gap.missing_to_ms,
                    gap.bar_count,
                    detected_ms,
                )
                for gap in gaps
            ],
        )
        return len(gaps)

    def resolve_gaps(self, symbol: str, timeframe: Timeframe, *, resolved_ms: int) -> int:
        """[쓰임] 이제 봉이 다 채워진 빈 구간을 '해소' 로 바꾸고, 바꾼 개수를 돌려준다.
        [이유] 빈 구간 안의 봉 수가 ``bar_count`` 이상일 때만 해소로 본다 — 일부만 채워진 구간을
               해소로 잘못 처리하지 않는다."""
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
        """[쓰임] 아직 해소되지 않은 빈 구간 수 (``timeframe`` 을 주면 그 타임프레임만).
        [이유] 0 이 아니면 진입을 거부한다 (게이트 G2.3) — 빠진 봉 위에서 판단하지 않는다."""
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
