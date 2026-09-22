"""무기한 선물 포지셔닝 지표 저장·조회.

캔들과 같은 인과성 규칙을 쓴다 — ``at_ms <= as_of_ms`` 인 스냅샷만 내보낸다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from decimal import Decimal
from itertools import pairwise

from auto_trading.core.types import PerpMetrics

SNAPSHOT_INTERVAL_MS = 300_000
"""Binance 가 제공하는 스냅샷 간격 (5분)."""

JITTER_TOLERANCE_MS = 60_000
"""결측 판정 허용 오차. funding 과 같은 이유다 (거래소 시각 지터)."""

__all__ = ["JITTER_TOLERANCE_MS", "SNAPSHOT_INTERVAL_MS", "PerpMetricsRepository"]

_COLUMNS = (
    "open_interest",
    "open_interest_value",
    "toptrader_account_ratio",
    "toptrader_position_ratio",
    "account_long_short_ratio",
    "taker_buy_sell_ratio",
)


def _row_to_metrics(row: sqlite3.Row) -> PerpMetrics:
    return PerpMetrics(
        at_ms=int(row["at_ms"]),
        **{name: Decimal(str(row[name])) for name in _COLUMNS},
    )


class PerpMetricsRepository:
    """``perp_metrics`` 테이블 접근."""

    __slots__ = ("_conn", "exchange")

    def __init__(self, conn: sqlite3.Connection, *, exchange: str = "binance_usdm") -> None:
        self._conn = conn
        self.exchange = exchange

    def upsert_many(self, symbol: str, entries: Iterable[PerpMetrics]) -> int:
        rows = [
            (self.exchange, symbol, m.at_ms, *(str(getattr(m, name)) for name in _COLUMNS))
            for m in entries
        ]
        if not rows:
            return 0
        assignments = ", ".join(f"{name} = excluded.{name}" for name in _COLUMNS)
        self._conn.executemany(
            f"INSERT INTO perp_metrics (exchange, symbol, at_ms, {', '.join(_COLUMNS)})"
            f" VALUES (?, ?, ?, {', '.join('?' * len(_COLUMNS))})"
            f" ON CONFLICT (exchange, symbol, at_ms) DO UPDATE SET {assignments}",
            rows,
        )
        return len(rows)

    def get_range(self, symbol: str, from_ms: int, to_ms: int) -> tuple[PerpMetrics, ...]:
        rows = self._conn.execute(
            "SELECT * FROM perp_metrics"
            " WHERE exchange = ? AND symbol = ? AND at_ms BETWEEN ? AND ?"
            " ORDER BY at_ms",
            (self.exchange, symbol, from_ms, to_ms),
        ).fetchall()
        return tuple(_row_to_metrics(r) for r in rows)

    def latest_as_of(self, symbol: str, as_of_ms: int) -> PerpMetrics | None:
        """``as_of_ms`` 시점에 알 수 있었던 가장 최근 스냅샷."""
        row = self._conn.execute(
            "SELECT * FROM perp_metrics"
            " WHERE exchange = ? AND symbol = ? AND at_ms <= ?"
            " ORDER BY at_ms DESC LIMIT 1",
            (self.exchange, symbol, as_of_ms),
        ).fetchone()
        return None if row is None else _row_to_metrics(row)

    def last_at_ms(self, symbol: str) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(at_ms) AS m FROM perp_metrics WHERE exchange = ? AND symbol = ?",
            (self.exchange, symbol),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def count(self, symbol: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM perp_metrics WHERE exchange = ? AND symbol = ?",
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
        """5분보다 크게 벌어진 구간. 결측 구간에서는 신호를 만들지 않는다."""
        entries = self.get_range(symbol, from_ms, to_ms)
        return tuple(
            (previous.at_ms, current.at_ms)
            for previous, current in pairwise(entries)
            if current.at_ms - previous.at_ms - SNAPSHOT_INTERVAL_MS > tolerance_ms
        )
