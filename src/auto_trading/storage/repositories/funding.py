"""funding rate(무기한 선물 정산 비율) 저장 · 조회 — ``funding_rates`` 표.

언제 쓰나: funding 백필(marketdata/funding)이 넣고, 스냅샷 · 백테스트가 '그 시점의 funding' 과 '보유
           중 오간 funding 합계' 를 읽을 때.
읽고 쓰는 것: ``funding_rates`` 표.

읽기 함수는 모두 ``as_of_ms`` 를 받고 **그 시점에 이미 정산된** 건만 돌려준다.
미래의 정산 내역이 스냅샷에 들어가면 look-ahead 다 (AGENTS.md §3.2).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from decimal import Decimal
from itertools import pairwise

from auto_trading.core.types import FundingRate

JITTER_TOLERANCE_MS = 60_000
"""거래소 정산 시각 지터 허용치. 이보다 늦어야 실제 누락으로 본다."""

__all__ = ["JITTER_TOLERANCE_MS", "FundingRateRepository"]


def _row_to_rate(row: sqlite3.Row) -> FundingRate:
    """[쓰임] DB 한 줄을 :class:`FundingRate` 로 바꾼다 (mark 가격은 없을 수 있다)."""
    mark = row["mark_price"]
    return FundingRate(
        funding_ms=int(row["funding_ms"]),
        rate=Decimal(str(row["rate"])),
        interval_hours=int(row["interval_hours"]),
        mark_price=None if mark is None else Decimal(str(mark)),
    )


class FundingRateRepository:
    """``funding_rates`` 표 읽기 · 쓰기 창구."""

    __slots__ = ("_conn", "exchange")

    def __init__(self, conn: sqlite3.Connection, *, exchange: str = "binance_usdm") -> None:
        self._conn = conn
        self.exchange = exchange

    def upsert_many(self, symbol: str, rates: Iterable[FundingRate]) -> int:
        """[쓰임] 정산 내역을 넣고 처리한 개수를 돌려준다. 같은 정산 시각이 있으면 새 값으로.
        [이유] 같은 달 파일을 다시 받아도 결과가 같아야 한다 (멱등 — 이어 받기가 안전해진다)."""
        rows = [
            (
                self.exchange,
                symbol,
                rate.funding_ms,
                str(rate.rate),
                None if rate.mark_price is None else str(rate.mark_price),
                rate.interval_hours,
            )
            for rate in rates
        ]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT INTO funding_rates"
            " (exchange, symbol, funding_ms, rate, mark_price, interval_hours)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (exchange, symbol, funding_ms) DO UPDATE SET"
            "   rate = excluded.rate,"
            "   mark_price = excluded.mark_price,"
            "   interval_hours = excluded.interval_hours",
            rows,
        )
        return len(rows)

    def latest_as_of(self, symbol: str, as_of_ms: int) -> FundingRate | None:
        """[쓰임] ``as_of_ms`` 시점에 **이미 정산된** 가장 최근 funding (없으면 None).
        [이유] funding 은 정산 시각에 값이 확정되므로 그 시각 이후에만 알 수 있다 —
               ``funding_ms <= as_of_ms`` 가 인과성 조건이다."""
        row = self._conn.execute(
            "SELECT * FROM funding_rates"
            " WHERE exchange = ? AND symbol = ? AND funding_ms <= ?"
            " ORDER BY funding_ms DESC LIMIT 1",
            (self.exchange, symbol, as_of_ms),
        ).fetchone()
        return None if row is None else _row_to_rate(row)

    def get_range(self, symbol: str, from_ms: int, to_ms: int) -> tuple[FundingRate, ...]:
        """[쓰임] ``[from_ms, to_ms]`` 구간의 정산 내역을 시간 순서로 (보유 중 funding 집계용)."""
        rows = self._conn.execute(
            "SELECT * FROM funding_rates"
            " WHERE exchange = ? AND symbol = ? AND funding_ms BETWEEN ? AND ?"
            " ORDER BY funding_ms",
            (self.exchange, symbol, from_ms, to_ms),
        ).fetchall()
        return tuple(_row_to_rate(row) for row in rows)

    def last_funding_ms(self, symbol: str) -> int | None:
        """[쓰임] 저장된 마지막 정산 시각 (없으면 None).
        [이유] 백필을 이어 받을 지점을 찾는 데 쓴다."""
        row = self._conn.execute(
            "SELECT MAX(funding_ms) AS m FROM funding_rates WHERE exchange = ? AND symbol = ?",
            (self.exchange, symbol),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def count(self, symbol: str) -> int:
        """[쓰임] 저장된 정산 내역 개수."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM funding_rates WHERE exchange = ? AND symbol = ?",
            (self.exchange, symbol),
        ).fetchone()
        return 0 if row is None else int(row["c"])

    def find_gaps(
        self,
        symbol: str,
        *,
        from_ms: int,
        to_ms: int,
        tolerance_ms: int = JITTER_TOLERANCE_MS,
    ) -> tuple[tuple[int, int], ...]:
        """[쓰임] 이웃한 두 정산 사이가 주기보다 벌어진 곳을 ``(앞 시각, 뒤 시각)`` 쌍으로 돌려준다.
        [이유] 각 행이 자기 주기를 들고 있으므로, 주기가 바뀐 시점(8h→4h 등)을 결측으로 오인하지
               않는다. ``tolerance_ms`` 가 필요한 이유 (2026-09-21 실측): Binance 의 ``calc_time``
               은 정각에서 수 밀리초씩 흔들린다. BTCUSDT 7,305건 중 **2,256건**이 8시간을 1~4ms
               초과했다. 정확히 비교하면 진짜 결측 0건인 데이터에서 2,256건을 오탐한다. 1분을 넘는
               지연만 실제 누락으로 본다."""
        rates = self.get_range(symbol, from_ms, to_ms)
        return tuple(
            (previous.funding_ms, current.funding_ms)
            for previous, current in pairwise(rates)
            if current.funding_ms - previous.next_funding_ms > tolerance_ms
        )

    def accrued_between(
        self, symbol: str, *, from_ms: int, to_ms: int, notional: Decimal, is_long: bool
    ) -> Decimal:
        """[쓰임] 보유 구간에서 실제로 오간 funding 합계 (낸 돈이 양수).
        [이유] 진입 시각 **이후** 첫 정산부터 청산 시각까지를 센다. 정산 시각 직전에 청산하면 한
               푼도 내지 않는다 — 구간 끝을 포함(``<=``)해야 그 경계가 맞는다."""
        total = Decimal(0)
        for rate in self.get_range(symbol, from_ms + 1, to_ms):
            total += notional * (rate.rate if is_long else -rate.rate)
        return total
