"""국내 주식 유니버스 Gold — 해마다 생존 편향을 숫자로 보이고, 두 출처의 수정주가를 맞댄다(K-3b).

언제 쓰나: 유니버스 Silver(:mod:`mdp.silver.kr_universe`)를 만든 뒤, Spark 이미지 안에서::

    docker compose --profile spark run --rm spark python -m mdp.gold.kr_universe

읽고 쓰는 것:
- 읽기: ``<lake>/silver/kr_universe_prices`` · ``kr_listings`` ·
  ``kr_daily_bars`` (새로 받은 한국투자증권 일봉)
- 쓰기 (``<lake>/gold/``, Delta, 표 전체를 다시 씀):
  ``kr_survivorship_yearly`` (해마다 연초 종목 수 · 그 해 폐지 수 · 전체 평균 수익률과
  생존 종목만의 평균), ``kr_price_source_check`` (받아 둔 가격 vs 새로 받은 일봉 — 같은 종목 ·
  날의 종가 · 거래량 대조), 실행 기록(작업 ``gold.kr_universe``)

생존 편향 셈 (``kr_survivorship_yearly``):

- 그 해의 종목 = 앞 해 마지막 거래일에 종가가 있던 종목(그 날 상장돼 있던 종목).
- 한 종목의 그 해 수익률 = 그 해 마지막 종가 ÷ 앞 해 마지막 종가 − 1.
  그 해에 폐지됐으면 폐지 전 마지막 종가.
- ``mean_return_all`` = 그 해 종목 전체의 단순 평균,
  ``mean_return_survivors`` = 그 중 **자료 끝(마지막 거래일)에도 상장돼 있는 종목만**의 평균.
  둘의 차이 = 생존 편향. 중앙값(``median_*``)도 함께 둔다.
- 시작 · 끝 종가가 5 원 미만(정밀도 잃음)인 종목 · 해는 빼고 ``excluded_tiny_price`` 로 센다
  — 한 종목의 뭉개진 수정주가가 평균을 +600 %p 흔들었다(2013 년).

[이유] 생존 편향은 '지금 살아 있는 종목만 모으면 과거가 좋아 보인다' 는 말로는 와닿지 않는다.
       같은 자료로 두 평균을 해마다 나란히 놓으면 크기가 보인다. 폐지 종목의 마지막 값은 폐지 전
       마지막 종가라 실제 손실(정리매매 · 0 원)보다 덜 나쁘게 잡힌다 — 그래서 이 숫자는 편향의
       **아래쪽 어림**이다. 수익률은 수정주가 종가로만 셈한다(배당은 넣지 않음).
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from typing import Any

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.silver.kr_stocks import write_table
from mdp.spark.session import spark_session, table_path

__all__ = ["main", "price_source_check", "run_gold", "survivorship_yearly"]


def survivorship_yearly(prices: Any, listings: Any, run_id: str) -> Any:
    """[쓰임] 해마다 한 줄 — 연초 종목 수 · 그 해 폐지 · 전체 평균 수익률 · 생존 종목만 평균 · 차이.
    [이유] '연초 종목' 을 앞 해 마지막 거래일(모든 종목 중 가장 늦은 날)의 종가로 정한다 — 그 날
           상장돼 있던 종목만 고르는 것이 '그 때 알 수 있었던 유니버스' 다. 자료가 끝난 해(마지막
           거래일이 12 월이 아닌 해)는 셈하지 않는다."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    dated = prices.withColumn("year", F.year("trade_date"))
    year_end = dated.groupBy("year").agg(F.max("trade_date").alias("year_end"))
    data_end = prices.agg(F.max("trade_date")).collect()[0][0]
    by_symbol_year = Window.partitionBy("symbol", "year").orderBy(F.col("trade_date").desc())
    last_close = (
        dated.withColumn("rank", F.row_number().over(by_symbol_year))
        .where(F.col("rank") == 1)
        .select(
            "symbol",
            "year",
            F.col("close").alias("last_close"),
            "trade_date",
            F.col("close_precision_lost").alias("last_tiny"),
        )
    )
    start = (
        last_close.join(year_end, "year")
        .where(F.col("trade_date") == F.col("year_end"))  # 앞 해 마지막 거래일에 종가가 있던 종목
        .select(
            "symbol",
            (F.col("year") + 1).alias("year"),
            F.col("last_close").alias("start_close"),
            F.col("last_tiny").alias("start_tiny"),
        )
    )
    life = listings.select("symbol", "delisted_on")
    full_years = year_end.where(F.month("year_end") == 12).select("year")
    members = (
        start.join(full_years, "year")
        .join(
            last_close.select("symbol", "year", "last_close", "last_tiny"),
            ["symbol", "year"],
            "left",
        )
        .join(life, "symbol", "left")
        .withColumn("tiny", F.col("start_tiny") | F.coalesce("last_tiny", F.lit(False)))
        .withColumn(
            "year_return", F.when(~F.col("tiny"), F.col("last_close") / F.col("start_close") - 1)
        )
        .withColumn(
            "survivor", F.col("delisted_on").isNull() | (F.col("delisted_on") > F.lit(data_end))
        )
        .withColumn(
            "delisted_in_year",
            F.col("delisted_on").isNotNull() & (F.year("delisted_on") == F.col("year")),
        )
    )
    return (
        members.groupBy("year")
        .agg(
            F.count(F.lit(1)).alias("members_at_start"),
            F.sum(F.col("delisted_in_year").cast("int")).alias("delisted_in_year"),
            F.sum(F.col("survivor").cast("int")).alias("survivors"),
            F.sum(F.col("last_close").isNull().cast("int")).alias("no_price_in_year"),
            F.sum(F.col("tiny").cast("int")).alias("excluded_tiny_price"),
            F.avg("year_return").cast("double").alias("mean_return_all"),
            F.avg(F.when(F.col("survivor"), F.col("year_return")))
            .cast("double")
            .alias("mean_return_survivors"),
            F.median("year_return").cast("double").alias("median_return_all"),
            F.median(F.when(F.col("survivor"), F.col("year_return")))
            .cast("double")
            .alias("median_return_survivors"),
        )
        .withColumn("survivorship_bias", F.col("mean_return_survivors") - F.col("mean_return_all"))
        .withColumn("data_end", F.lit(data_end))
        .withColumn("run_id", F.lit(run_id))
        .orderBy("year")
    )


def price_source_check(prices: Any, daily: Any, run_id: str) -> Any:
    """[쓰임] 받아 둔 수정주가 종가 vs 새로 받은 한국투자증권 일봉(수정주가)
    — 겹치는 종목 · 날마다 한 줄.
    [이유] 같은 원천에서 두 번(원본 저장소가 2026-08 에, 이 저장소가 2026-10 에) 받은 값이 같은지
           본다. 종가가 다르면 그 사이 분할 · 분배로 수정 기준이 바뀐 것이다(ETF 는 그럴 수 있다).
           거래량은 받아 둔 쪽이 6 자리로 반올림됐는지(``volume_rounded``)와 함께 상대 차이를
           남겨, 반올림이 얼마나 틀렸는지 잰다."""
    from pyspark.sql import functions as F

    fresh = daily.where(F.col("price_basis") == "adjusted").select(
        "symbol",
        "trade_date",
        F.col("close").alias("fresh_close"),
        F.col("volume").alias("fresh_volume"),
    )
    return (
        prices.select(
            "symbol",
            "trade_date",
            F.col("close").alias("imported_close"),
            F.col("volume").alias("imported_volume"),
            "close_rounded",
            "volume_rounded",
        )
        .join(fresh, ["symbol", "trade_date"])
        .withColumn("same", F.col("imported_close") == F.col("fresh_close"))
        .withColumn("volume_same", F.col("imported_volume") == F.col("fresh_volume"))
        .withColumn(
            "volume_relative_gap",
            F.when(
                F.col("fresh_volume") > 0,
                F.abs(F.col("imported_volume") - F.col("fresh_volume")) / F.col("fresh_volume"),
            ).cast("double"),
        )
        .withColumn("run_id", F.lit(run_id))
    )


def run_gold(recorder: RunRecorder) -> dict[str, tuple[int, int]]:
    """[쓰임] 유니버스 Gold 표 둘을 다시 만들고 실행 기록에 남긴다."""
    written: dict[str, tuple[int, int]] = {}
    with record_run(recorder, "gold.kr_universe", {}) as run:
        spark = spark_session("gold.kr_universe")
        spark.sparkContext.setLogLevel("ERROR")

        def load(name: str) -> Any:
            return spark.read.format("delta").load(table_path("silver", name))

        prices = load("kr_universe_prices").cache()
        outputs = {
            "kr_survivorship_yearly": survivorship_yearly(
                prices, load("kr_listings"), str(run.run_id)
            ),
            "kr_price_source_check": price_source_check(
                prices, load("kr_daily_bars"), str(run.run_id)
            ),
        }
        for name, df in outputs.items():
            version, rows = write_table(df, "gold", name)
            written[name] = (version, rows)
            run.add(
                Artifact(f"gold/{name}", "gold", "rebuilt", row_count=rows, table_version=version)
            )
            log_json(event="table", table=f"gold/{name}", version=version, rows=rows)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.gold.kr_universe")
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run_gold(recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        run_gold(NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
