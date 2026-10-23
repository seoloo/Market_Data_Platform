"""Gold 1 분봉 시험 — 작은 체결로 시가 · 고가 · 저가 · 종가 · 거래량 · 체결 수와 공식 봉 대조.

Spark 이미지 안에서만 돈다(호스트에는 pyspark 가 없어 건너뜀).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pyspark")

from mdp.gold.bars_1m import build_bars, check_against_klines
from mdp.spark.session import spark_session

T0 = 1_709_251_200_000  # 2024-03-01 00:00 UTC
SCHEMA = (
    "symbol string, agg_trade_id long, price decimal(18,8), quantity decimal(18,8),"
    " first_trade_id long, last_trade_id long, transact_time_ms long, is_buyer_maker boolean"
)
TRADES = [
    # 00:00 분 — 같은 밀리초(T0+5)에 체결 둘: 번호가 작은 11 이 먼저(시가), 12 가 나중
    ("BTCUSDT", 11, Decimal("100"), Decimal("1.0"), 101, 103, T0 + 5, False),
    ("BTCUSDT", 12, Decimal("105"), Decimal("2.0"), 104, 104, T0 + 5, True),
    ("BTCUSDT", 13, Decimal("95"), Decimal("0.5"), 105, 106, T0 + 30_000, False),
    ("BTCUSDT", 14, Decimal("101"), Decimal("1.5"), 107, 107, T0 + 59_999, True),
    # 00:01 분 — 체결 하나
    ("BTCUSDT", 15, Decimal("102"), Decimal("3.0"), 108, 110, T0 + 60_000, False),
]


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    session = spark_session("test-gold")
    session.sparkContext.setLogLevel("ERROR")
    yield session


def test_bars_have_correct_ohlc_volumes_and_counts(spark: Any) -> None:
    bars = {
        r["bar_start_ms"]: r
        for r in build_bars(spark.createDataFrame(TRADES, SCHEMA), 7, "r").collect()
    }
    first = bars[T0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (
        Decimal("100"),
        Decimal("105"),
        Decimal("95"),
        Decimal("101"),
    )
    assert first["volume"] == Decimal("5.0") and first["taker_buy_volume"] == Decimal("1.5")
    assert first["quote_volume"] == Decimal("100") + Decimal("210") + Decimal("47.5") + Decimal(
        "151.5"
    )
    assert first["trade_count"] == 3 + 1 + 2 + 1 and first["agg_trade_count"] == 4
    assert first["silver_version"] == 7 and str(first["trade_date"]) == "2024-03-01"
    second = bars[T0 + 60_000]
    assert second["open"] == second["close"] == Decimal("102") and second["trade_count"] == 3
    assert len(bars) == 2  # 체결 없는 분은 줄이 없다


def test_kline_check_counts_equal_minutes(spark: Any) -> None:
    bars = build_bars(spark.createDataFrame(TRADES, SCHEMA), 1, "r")
    klines = spark.createDataFrame(
        [  # 00:00 은 칸마다 같고, 00:01 은 거래량만 다르다
            (str(T0), "100", "105", "95", "101", "5.0", "7", "1.5"),
            (str(T0 + 60_000), "102", "102", "102", "102", "2.9", "3", "3.0"),
        ],
        "open_time string, open string, high string, low string, close string, volume string,"
        " count string, taker_buy_volume string",
    )
    row = check_against_klines(bars, klines, "BTCUSDT", "r").collect()[0]
    assert (row["gold_minutes"], row["kline_minutes"]) == (2, 2)
    assert row["all_fields_equal"] == 1 and row["ohlc_equal"] == 2 and row["volume_equal"] == 1
    assert row["gold_volume"] == Decimal("8.0") and row["kline_volume"] == Decimal("7.9")
    assert row["symbol"] == "BTCUSDT"
