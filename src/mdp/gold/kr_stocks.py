"""국내 주식 Gold — 분봉으로 만든 하루 봉을 한국투자증권 일봉과 맞대고, 수정주가 사건을 찾는다(K-3).

언제 쓰나: Silver(:mod:`mdp.silver.kr_stocks`)를 만든 뒤, Spark 이미지 안에서::

    docker compose --profile spark run --rm spark python -m mdp.gold.kr_stocks

읽고 쓰는 것:
- 읽기: ``<lake>/silver/kr_minute_bars`` · ``kr_daily_bars``
- 쓰기 (``<lake>/gold/``, Delta, 표 전체를 다시 씀):
  ``kr_daily_from_minutes`` (종목 · 날마다 분봉으로 만든 시고저종 · 거래량과 일봉 대조 결과) ·
  ``kr_adjustment_events`` (원주가 ÷ 수정주가 비율이 바뀐 날 = 분할 · 병합 · 분배 같은 사건),
  실행 기록(작업 ``gold.kr_stocks``, 표마다 Delta 버전 · 읽은 Silver 버전)

대조 결과 (``kr_daily_from_minutes.result``):

| 결과 | 뜻 |
|---|---|
| ``match_raw`` | 분봉 시고저종 = 일봉 원주가 시고저종 (넷 다) |
| ``match_adjusted`` | 원주가와는 다르고 수정주가와 넷 다 같다 |
| ``no_closing_bar`` | 마감 동시호가 봉이 없어 종가만 다르고 시고저는 원주가와 같다 |
| ``price_basis_differs`` | 넷 다 원주가와 다르다 — 분봉이 수정 가격으로 온 것으로 보인다(ETF) |
| ``other`` | 그 밖 |
| ``no_daily`` | 그 날 일봉을 아직 받지 않았다 |

[이유] 원천이 체결 이력을 주지 않아 '정답 체결' 과 맞댈 수 없다. 대신 같은 원천의 두 표(분봉 ·
       일봉)가 서로 맞는지를 날마다 보고, 다르면 원인 이름을 붙인다. 시고저종을 하나씩 따로 맞대야
       '종가만 다름(마감 봉 없음)' 과 '가격 기준이 다름' 을 가를 수 있다. 거래량은 분봉 합이
       일봉보다 작은 것이 보통이라(2026-10-26 확인 — 시간외 등으로 보임, 확인 필요) '같음' 이
       아니라 차이를 남긴다.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from typing import Any, Final

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.silver.kr_stocks import write_table
from mdp.spark.session import spark_session, table_path

__all__ = ["EVENT_TOLERANCE", "adjustment_events", "daily_from_minutes", "main", "run_gold"]

EVENT_TOLERANCE: Final = 0.002
"""원주가 ÷ 수정주가 비율이 앞 거래일보다 0.2 % 넘게 바뀌면 사건으로 본다.
ETF 수정주가는 반올림 때문에 비율이 날마다 0.001 % 쯤 흔들린다(KODEX 200 2015-01, 2026-10-26
확인). 0.2 % 보다 작은 분배는 사건으로 잡히지 않는다."""


def daily_from_minutes(minutes: Any, daily: Any, run_id: str) -> Any:
    """[쓰임] 종목 · 날마다 분봉(정규장 + 마감 봉)으로 시고저종을 만들고 일봉 원주가 · 수정주가와
    맞댄다.
    [이유] 시가 = 첫 정규 봉의 시가, 종가 = 마감 동시호가 봉(없으면 마지막 정규 봉)의 종가. 장 후
           봉은 일봉 가격에 들어가지 않는다(2026-10-26 확인 — 두 주식 470 일 중 464 일이 이 규칙으로
           넷 다 같다)."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    day_rows = minutes.where(F.col("session").isin("regular", "closing_auction"))
    ordered = Window.partitionBy("symbol", "trade_date").orderBy("minute_of_day")
    whole = ordered.rowsBetween(Window.unboundedPreceding, Window.unboundedFollowing)
    built = (
        day_rows.withColumn("first_open", F.first("open").over(whole))
        .withColumn("last_close", F.last("close").over(whole))
        .groupBy("symbol", "trade_date")
        .agg(
            F.first("first_open").alias("m_open"),
            F.max("high").alias("m_high"),
            F.min("low").alias("m_low"),
            F.first("last_close").alias("m_close"),
            F.max(F.when(F.col("session") == "closing_auction", True)).alias("has_closing_bar"),
        )
        .fillna(False, ["has_closing_bar"])
    )
    volumes = minutes.groupBy("symbol", "trade_date").agg(
        F.sum("volume").alias("m_volume"),
        F.sum(F.when(F.col("session") == "after_hours", F.col("volume")).otherwise(0)).alias(
            "m_after_hours_volume"
        ),
    )

    def basis(name: str) -> Any:
        return daily.where(F.col("price_basis") == name).select(
            "symbol",
            "trade_date",
            *(F.col(c).alias(f"{name}_{c}") for c in ("open", "high", "low", "close", "volume")),
        )

    joined = (
        built.join(volumes, ["symbol", "trade_date"])
        .join(basis("raw"), ["symbol", "trade_date"], "left")
        .join(basis("adjusted"), ["symbol", "trade_date"], "left")
    )

    def same(prefix: str, part: str) -> Any:
        return F.coalesce(F.col(f"m_{part}") == F.col(f"{prefix}_{part}"), F.lit(False))

    raw = {p: same("raw", p) for p in ("open", "high", "low", "close")}
    adjusted = {p: same("adjusted", p) for p in ("open", "high", "low", "close")}
    result = (
        F.when(F.col("raw_close").isNull(), "no_daily")
        .when(raw["open"] & raw["high"] & raw["low"] & raw["close"], "match_raw")
        .when(
            adjusted["open"] & adjusted["high"] & adjusted["low"] & adjusted["close"],
            "match_adjusted",
        )
        .when(~F.col("has_closing_bar") & raw["open"] & raw["high"] & raw["low"], "no_closing_bar")
        .when(~(raw["open"] | raw["high"] | raw["low"] | raw["close"]), "price_basis_differs")
        .otherwise("other")
    )
    return (
        joined.withColumn("result", result)
        .withColumn("open_matches_raw", raw["open"])
        .withColumn("high_matches_raw", raw["high"])
        .withColumn("low_matches_raw", raw["low"])
        .withColumn("close_matches_raw", raw["close"])
        .withColumn("volume_gap", F.col("raw_volume") - F.col("m_volume"))
        .withColumn("close_ratio_to_raw", (F.col("m_close") / F.col("raw_close")).cast("double"))
        .withColumn("run_id", F.lit(run_id))
    )


def adjustment_events(daily: Any, run_id: str) -> Any:
    """[쓰임] 종목마다 원주가 ÷ 수정주가(종가) 비율이 앞 거래일보다 :data:`EVENT_TOLERANCE` 넘게
    바뀐 날.
    [이유] 원천 일봉의 분할 표시 칸(``prtt_rate`` · ``mod_yn``)은 분할 날(삼성전자 2018-05-04)에도
           비어 있었다(2026-10-26 확인). 그래서 표시가 아니라 두 가격의 비율이 바뀌는 날로 사건을
           찾는다. ``step`` 은 앞 비율 ÷ 새 비율 — 50:1 분할이면 50."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    raw = daily.where(F.col("price_basis") == "raw").select(
        "symbol", "trade_date", F.col("close").alias("raw_close")
    )
    adjusted = daily.where(F.col("price_basis") == "adjusted").select(
        "symbol", "trade_date", F.col("close").alias("adjusted_close")
    )
    ordered = Window.partitionBy("symbol").orderBy("trade_date")
    factors = (
        raw.join(adjusted, ["symbol", "trade_date"])
        .where(F.col("adjusted_close") > 0)
        .withColumn("factor", (F.col("raw_close") / F.col("adjusted_close")).cast("double"))
        .withColumn("previous_factor", F.lag("factor").over(ordered))
        .withColumn("previous_date", F.lag("trade_date").over(ordered))
    )
    return (
        factors.where(
            F.col("previous_factor").isNotNull()
            & (F.abs(F.col("previous_factor") / F.col("factor") - 1) > EVENT_TOLERANCE)
        )
        .withColumn("step", F.col("previous_factor") / F.col("factor"))
        .select(
            "symbol",
            "trade_date",
            "previous_date",
            "previous_factor",
            "factor",
            "step",
            "raw_close",
            "adjusted_close",
        )
        .withColumn("run_id", F.lit(run_id))
    )


def _version(spark: Any, layer: str, name: str) -> int:
    """[쓰임] Delta 표의 지금 버전 — 어느 Silver 로 만들었는지 남긴다."""
    from delta.tables import DeltaTable

    table = DeltaTable.forPath(spark, table_path(layer, name))
    return int(table.history(1).collect()[0]["version"])


def run_gold(recorder: RunRecorder) -> dict[str, tuple[int, int]]:
    """[쓰임] Silver 로 Gold 표 둘을 다시 만들고 실행 기록에 남긴다."""
    written: dict[str, tuple[int, int]] = {}
    with record_run(recorder, "gold.kr_stocks", {}) as run:
        spark = spark_session("gold.kr_stocks")
        spark.sparkContext.setLogLevel("ERROR")
        minute_version = _version(spark, "silver", "kr_minute_bars")
        daily_version = _version(spark, "silver", "kr_daily_bars")
        minutes = spark.read.format("delta").load(table_path("silver", "kr_minute_bars"))
        daily = spark.read.format("delta").load(table_path("silver", "kr_daily_bars")).cache()
        outputs = {
            "kr_daily_from_minutes": daily_from_minutes(minutes, daily, str(run.run_id)),
            "kr_adjustment_events": adjustment_events(daily, str(run.run_id)),
        }
        status = f"from_silver_minute_v{minute_version}_daily_v{daily_version}"
        for name, df in outputs.items():
            version, rows = write_table(df, "gold", name)
            written[name] = (version, rows)
            run.add(Artifact(f"gold/{name}", "gold", status, row_count=rows, table_version=version))
            log_json(event="table", table=f"gold/{name}", version=version, rows=rows, silver=status)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.gold.kr_stocks")
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
