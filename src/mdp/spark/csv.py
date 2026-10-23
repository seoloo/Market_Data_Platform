"""원천 CSV 를 글자 그대로 읽기 — Silver · Gold 가 함께 쓴다.

언제 쓰나: Bronze 에서 풀어 놓은 체결 · 1 분봉 CSV 를 Spark 로 읽을 때.
읽고 쓰는 것: CSV 파일(읽기만).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

__all__ = ["AGG_TRADE_COLUMNS", "KLINE_COLUMNS", "read_strings"]

AGG_TRADE_COLUMNS: Final = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)
"""바이낸스 체결(aggTrades) CSV 칸 — 머리줄이 있는 파일 · 없는 파일 모두 이 순서."""
KLINE_COLUMNS: Final = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
"""바이낸스 봉(klines) CSV 칸."""


def read_strings(spark: Any, csv_paths: Sequence[str], columns: Sequence[str]) -> Any:
    """[쓰임] CSV 들을 모든 칸 글자로 읽고, 줄마다 어느 파일에서 왔는지(``csv_name``)를 붙인다.
              형식이 틀린 줄은 ``_corrupt`` 칸에 통째로 담긴다.
    [이유] 형식이 틀린 줄을 Spark 가 조용히 버리지 않도록 PERMISSIVE + ``_corrupt`` 로 받고, 타입은
           부르는 쪽이 ``try_cast`` 로 맞춘다(실패한 줄은 격리할 수 있게)."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    schema = StructType([StructField(name, StringType()) for name in (*columns, "_corrupt")])
    return (
        spark.read.schema(schema)
        .option("header", "false")
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", "_corrupt")
        .csv(list(csv_paths))
        .withColumn("csv_name", F.element_at(F.split(F.col("_metadata.file_path"), "/"), -1))
    )
