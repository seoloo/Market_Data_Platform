"""매일 대사 시험 — 분마다 상태 · 원인 나누기 (작은 체결로 상태마다 한 분씩).

Spark 이미지 안에서만 돈다.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pyspark")

from mdp.gold.bars_1m import build_bars
from mdp.reconcile.daily import classify_minutes, summarize
from mdp.spark.session import spark_session

T0 = 1_709_251_200_000  # 2024-03-01 00:00 UTC
M = 60_000
SCHEMA = (
    "symbol string, agg_trade_id long, price decimal(18,8), quantity decimal(18,8),"
    " first_trade_id long, last_trade_id long, transact_time_ms long, is_buyer_maker boolean"
)


def _trade(agg_id: int, minute: int, price: str = "100") -> tuple[Any, ...]:
    """분 ``minute`` 의 체결 하나 (분마다 번호 2 개: 10·minute, 10·minute + 1)."""
    offset = (agg_id % 10) * 1000
    return (
        "BTCUSDT",
        agg_id,
        Decimal(price),
        Decimal("1"),
        agg_id,
        agg_id,
        T0 + minute * M + offset,
        False,
    )


# 배치(= 진짜): 분 0 ~ 6 마다 체결 둘
BATCH = [_trade(10 * m + k, m) for m in range(7) for k in (0, 1)]
STREAM = [
    *[_trade(10 * 0 + k, 0) for k in (0, 1)],  # 0 분: 같음 → matched
    # 1 분: 스트림에 봉 없음, 원문에도 없음 → stream_gap
    _trade(20, 2),  # 2 분: 21 이 빠짐, 원문에는 있음 → late
    *[_trade(30 + k, 3) for k in (0, 1, 1)],  # 3 분: 31 을 두 번 셈 → duplicate
    _trade(40, 4),
    _trade(41, 4, price="999"),  # 4 분: 수는 같은데 값이 다름 → other
    *[_trade(50 + k, 5) for k in (0, 1)],  # 5 분: 같고 메운 분 → repaired
    _trade(60, 6),  # 6 분: 61 이 빠짐, 원문에도 없음(받기 시작 전) → stream_gap
    _trade(70, 7),  # 7 분: 배치에 없음 → only_stream
]
# 원문 토픽에 들어온 번호 — 10 · 11 (1 분) · 61 (6 분)은 받지 못함
RECEIVED = (0, 1, 20, 21, 30, 31, 40, 41, 50, 51, 60, 70)


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    session = spark_session("test-reconcile")
    session.sparkContext.setLogLevel("ERROR")
    yield session


def test_each_minute_gets_its_cause(spark: Any) -> None:
    from pyspark.sql import functions as F

    batch_trades = spark.createDataFrame(BATCH, SCHEMA)
    batch_bars = build_bars(batch_trades, 0, "b")
    stream_bars = build_bars(spark.createDataFrame(STREAM, SCHEMA), 0, "s").withColumn(
        "repaired", F.col("bar_start_ms") == T0 + 5 * M
    )
    received = spark.createDataFrame(
        [("BTCUSDT", i) for i in RECEIVED], "symbol string, agg_trade_id long"
    )

    rows = {
        (r["bar_start_ms"] - T0) // M: r.asDict()
        for r in classify_minutes(
            batch_bars,
            stream_bars,
            batch_trades.select("symbol", "agg_trade_id", "transact_time_ms"),
            received,
        ).collect()
    }
    assert {m: r["status"] for m, r in rows.items()} == {
        0: "matched",
        1: "stream_gap",
        2: "late",
        3: "duplicate",
        4: "other",
        5: "repaired",
        6: "stream_gap",
        7: "only_stream",
    }
    assert (rows[1]["not_received"], rows[1]["late_trades"], rows[1]["stream_agg_trades"]) == (
        2,
        0,
        None,
    )
    assert (rows[2]["not_received"], rows[2]["late_trades"]) == (0, 1)
    assert (rows[6]["not_received"], rows[6]["late_trades"]) == (1, 0)
    assert (
        "high" in rows[4]["differing_columns"]
        and "agg_trade_count" not in rows[4]["differing_columns"]
    )
    assert rows[1]["differing_columns"] == []  # 한쪽에만 있으면 칸 비교 없음

    summary = summarize(list(rows.values()))
    assert summary["batch_minutes"] == 7 and summary["stream_minutes"] == 7
    assert (summary["matched"], summary["repaired"], summary["stream_gap"], summary["late"]) == (
        1,
        1,
        2,
        1,
    )
    assert (summary["duplicate"], summary["other"], summary["only_stream"]) == (1, 1, 1)
    assert (summary["trades_not_received"], summary["trades_late"]) == (3, 1)
