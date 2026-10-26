"""국내 주식 유니버스 Silver · Gold 시험 — 가격 격리 · 종목 감사 · 생존 편향 셈 · 두 출처 대조.

Spark 이미지 안에서만 돈다(호스트에는 pyspark 가 없어 건너뜀).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pyspark")

from pyspark.sql import functions as F

from mdp.gold.kr_universe import price_source_check, survivorship_yearly
from mdp.silver.kr_universe import audit_symbols, parse_prices
from mdp.spark.session import spark_session

LISTING_SCHEMA = (
    "symbol STRING, name STRING, listed_on DATE, delisted_on DATE, delist_reason_group STRING"
)


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    session = spark_session("test-kr-universe")
    session.sparkContext.setLogLevel("ERROR")
    yield session


def _raw(spark: Any, rows: list[tuple[str, str, str]], volume: str = "1") -> Any:
    return spark.createDataFrame(
        [
            (f"file:///lake/bronze/x/prices/{s}.csv", d, c, volume, "3.19851e+08")
            for s, d, c in rows
        ],
        "file_path STRING, date STRING, close STRING, volume STRING, value STRING",
    )


def test_exponent_numbers_are_read_and_marked_rounded(spark: Any) -> None:
    listings = _listings(spark, [("000020", None, None)])
    raw = _raw(spark, [("000020", "20070613", "1.4245e+06")], volume="1.02165e+06")
    clean, quarantine = parse_prices(raw, listings, "r")
    [row] = clean.collect()
    assert (row["close"], row["volume"]) == (Decimal("1424500.00"), 1_021_650)
    assert row["close_rounded"] and row["volume_rounded"] and row["value_rounded"]
    assert row["close_precision_lost"] is False
    assert quarantine.count() == 0


def _listings(spark: Any, rows: list[tuple[str, date | None, date | None]]) -> Any:
    return spark.createDataFrame(
        [(s, s, listed, delisted, None) for s, listed, delisted in rows], LISTING_SCHEMA
    )


def test_prices_outside_life_or_duplicated_are_quarantined(spark: Any) -> None:
    listings = _listings(
        spark, [("000020", date(2007, 1, 1), None), ("000030", date(2007, 1, 1), date(2008, 1, 31))]
    )
    raw = _raw(
        spark,
        [
            ("000020", "20070102", "6410"),
            ("000020", "20070103", "0"),
            ("000020", "20070104", "100"),
            ("000020", "20070104", "101"),
            ("000030", "20080131", "50"),
            ("000030", "20080201", "49"),
        ],
    )
    clean, quarantine = parse_prices(raw, listings, "r")
    assert sorted((r["symbol"], str(r["trade_date"])) for r in clean.collect()) == [
        ("000020", "2007-01-02"),
        ("000030", "2008-01-31"),
    ]
    [first] = [r for r in clean.collect() if r["symbol"] == "000020"]
    assert first["close"] == Decimal("6410.00") and first["value"] == pytest.approx(3.19851e8)
    assert sorted(r["reason"] for r in quarantine.collect()) == [
        "after_delisting",
        "duplicate_date",
        "duplicate_date",
        "non_positive_close",
    ]


def test_audit_flags_long_silence_and_missing_prices(spark: Any) -> None:
    listings = _listings(
        spark,
        [
            ("000470", date(1976, 1, 1), date(2017, 7, 15)),
            ("000790", date(2000, 1, 1), None),
            ("005930", date(1975, 6, 11), None),
            ("008080", date(1987, 12, 21), date(2013, 9, 25)),
        ],
    )
    prices = _prices(
        spark,
        [
            ("000470", date(2012, 7, 13), 5),
            ("005930", date(2026, 8, 31), 9),
            ("008080", date(2012, 12, 28), 1),
        ],
    )
    got = {r["symbol"]: r for r in audit_symbols(prices, listings, "r").collect()}
    assert got["000470"]["status"] == "long_silence_before_delisting"
    assert (
        got["000470"]["days_silent_before_delisting"]
        == (date(2017, 7, 15) - date(2012, 7, 13)).days
    )
    assert got["000790"]["status"] == "no_prices"
    assert got["005930"]["status"] == "ok" and got["005930"]["days_silent_before_delisting"] is None
    assert got["008080"]["status"] == "long_silence_before_delisting"  # 끊김 판정이 먼저
    assert got["008080"]["tiny_close_days"] == 1


def _prices(spark: Any, rows: list[tuple[str, date, int]]) -> Any:
    return spark.createDataFrame(
        [(s, d, Decimal(c), c < 5) for s, d, c in rows],
        "symbol STRING, trade_date DATE, close DECIMAL(18,2), close_precision_lost BOOLEAN",
    )


def test_tiny_adjusted_prices_are_left_out_and_counted(spark: Any) -> None:
    prices = _prices(
        spark,
        [
            ("A", date(2012, 12, 28), 1),
            ("A", date(2013, 9, 24), 4500),
            ("B", date(2012, 12, 28), 100),
            ("B", date(2013, 12, 30), 110),
        ],
    )
    listings = _listings(spark, [("A", None, date(2013, 9, 25)), ("B", None, None)])
    [row] = survivorship_yearly(prices, listings, "r").collect()
    assert (row["members_at_start"], row["excluded_tiny_price"]) == (2, 1)
    assert row["mean_return_all"] == pytest.approx(0.1)  # 1 원 → 4,500 원은 빠진다
    assert row["median_return_all"] == pytest.approx(0.1)


def test_survivorship_counts_the_dead_in_the_year_they_die(spark: Any) -> None:
    # 2008 년: 셋이 연초에 있음 — A 는 두 배, B 는 그대로, C 는 반 토막 나고 6 월에 폐지
    prices = _prices(
        spark,
        [
            ("A", date(2007, 12, 28), 100),
            ("A", date(2008, 12, 30), 200),
            ("B", date(2007, 12, 28), 100),
            ("B", date(2008, 12, 30), 100),
            ("C", date(2007, 12, 28), 100),
            ("C", date(2008, 6, 2), 50),
            ("D", date(2007, 12, 20), 100),
            ("D", date(2008, 12, 30), 300),
        ],
    )
    listings = _listings(
        spark,
        [("A", None, None), ("B", None, None), ("C", None, date(2008, 6, 30)), ("D", None, None)],
    )
    [row] = survivorship_yearly(prices, listings, "r").collect()  # 2007 년은 앞 해가 없어 빠진다
    assert (row["year"], row["members_at_start"], row["delisted_in_year"], row["survivors"]) == (
        2008,
        3,
        1,
        2,
    )  # D 는 앞 해 마지막 거래일(12-28)에 종가가 없어 빠진다
    assert row["mean_return_all"] == pytest.approx((1.0 + 0.0 - 0.5) / 3)
    assert row["mean_return_survivors"] == pytest.approx((1.0 + 0.0) / 2)
    assert row["survivorship_bias"] == pytest.approx(0.5 - 1 / 6)


def test_unfinished_last_year_is_not_counted(spark: Any) -> None:
    prices = _prices(spark, [("A", date(2025, 12, 30), 100), ("A", date(2026, 8, 31), 120)])
    assert survivorship_yearly(prices, _listings(spark, [("A", None, None)]), "r").count() == 0


def test_price_source_check_compares_same_symbol_and_day(spark: Any) -> None:
    prices = spark.createDataFrame(
        [
            ("005930", date(2018, 5, 4), Decimal(51900)),
            ("069500", date(2025, 10, 16), Decimal(100)),
        ],
        "symbol STRING, trade_date DATE, close DECIMAL(18,2)",
    )
    prices = (
        prices.withColumn("volume", F.lit(39_565_400).cast("long"))
        .withColumn("close_rounded", F.lit(False))
        .withColumn("volume_rounded", F.lit(True))
    )
    daily = spark.createDataFrame(
        [
            ("005930", date(2018, 5, 4), "adjusted", Decimal(51900), 39_565_391),
            ("005930", date(2018, 5, 4), "raw", Decimal(1), 1),
            ("069500", date(2025, 10, 16), "adjusted", Decimal(99), 39_565_400),
        ],
        "symbol STRING, trade_date DATE, price_basis STRING, close DECIMAL(18,2), volume BIGINT",
    )
    got = {r["symbol"]: r for r in price_source_check(prices, daily, "r").collect()}
    assert (got["005930"]["same"], got["069500"]["same"]) == (True, False)
    assert got["005930"]["volume_same"] is False
    assert got["005930"]["volume_relative_gap"] == pytest.approx(9 / 39_565_391)
