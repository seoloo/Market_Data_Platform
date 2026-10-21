"""Silver 체결(aggTrades) — 한 달치 Bronze zip 을 정제 · 감사해 Delta 표 다섯 개로 나눠 쓴다.

언제 쓰나: Bronze 에 그 달 체결이 있을 때. Spark 이미지 안에서::

    docker compose run --rm spark python -m mdp.silver.agg_trades --symbol BTCUSDT --month 2024-03

읽고 쓰는 것:
- 읽기: 체크섬 목록 · Bronze zip (읽기 직전 SHA-256 다시 대조) ·
  같은 달 1 분봉(있으면, 거래량 대조용)
- 쓰기 (``<lake>/silver/``, 모두 Delta · 그 달 칸만 바꿔 끼움):
  ``agg_trades``(깨끗한 체결) · ``agg_trades_quarantine``(못 쓰는 줄과 이유) ·
  ``agg_trades_audit_file``(원본 파일마다 결함 집계) · ``agg_trades_gaps``(빈 곳 · 역행 위치) ·
  ``agg_trades_audit_daily``(날마다 행 수 · 거래량 · 1 분봉 대조),
  비교 보고 JSON(``silver/_reports``),
  실행 기록(작업 ``silver.agg_trades``, 표마다 Delta 버전)

흐름 (:func:`build_outputs`):
1. 읽기 — CSV 를 모두 글자로 읽는다(형식이 틀린 줄도 버리지 않고 잡으려고).
2. 형식 맞추기 — 숫자 · 소수(decimal, 돈 계산에 float 를 쓰지 않는다) · 참거짓으로,
   마이크로초 시각은 ms 로.
3. 나누기 — 못 읽은 줄 · 파일 기간 밖 줄 · 같은 번호인데 내용이 다른 줄은 격리,
   일 파일에 넘긴 날은 뺀다.
4. 중복 — 내용까지 같은 중복은 하나만 남기고 몇 개였는지 센다.
5. 감사 — 파일마다 빈 곳 · 중복 · 시각 역행, 날마다 거래량을 공식 1 분봉 합계와 대조.

[이유] 연구 216 은 이 감사를 스크립트 안에서 숫자로만 남기고 원본을 지웠다(다시 못 함). 여기서는
       깨끗한 결과 · 버린 것 · 찾은 결함을 모두 표로 남기고, Bronze 가 그대로라 규칙을 바꾸면 다시
       만들 수 있다.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from mdp.bronze.archive import Dataset, Granularity
from mdp.bronze.fetch import (
    DEFAULT_MANIFEST,
    default_bronze_root,
    log_json,
    read_manifest,
    setup_cli_logging,
)
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.silver.sources import SourcePlan, plan_month_sources, stage_csv, verify_bronze_file
from mdp.spark.session import lake_root, spark_session, table_path

__all__ = ["SilverOutputs", "build_outputs", "main", "run_silver", "write_outputs"]

RAW_COLUMNS: Final = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)
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
MICROSECOND_THRESHOLD: Final = 10**14
"""이보다 큰 시각은 마이크로초 표기로 본다 — 연구 216(``_MICROSECOND_THRESHOLD``)과 같은 기준."""
VOLUME_TOLERANCE_PCT: Final = 0.5
"""날 거래량이 공식 1 분봉 합계와 이만큼(%) 넘게 다르면 의심일 — 연구 216 의 0.5 % 기준을 따랐다."""
ID_BUCKET: Final = 1_000_000
"""빈 곳을 찾을 때 번호를 이 크기로 나눠 여러 스레드가 나눠 정렬하게 한다(아래 :func:`_gaps`)."""
MINUTE_MS: Final = 60_000
HOUR_MS: Final = 3_600_000
DECIMAL: Final = "decimal(18,8)"


@dataclass(frozen=True, slots=True)
class SilverOutputs:
    """[쓰임] 한 달치 결과 DataFrame 다섯 개 (아직 쓰기 전)."""

    trades: Any
    quarantine: Any
    audit_file: Any
    gaps: Any
    audit_daily: Any
    check_minutes: Any
    """파일마다 매시 00 분 [거래량 · 공격 매수량 · 체결 수].

    연구 216 추출과 비교하는 값이다(표로 쓰지 않음)."""


def _strings_schema(columns: Sequence[str]) -> Any:
    """[쓰임] 모든 칸을 글자로 읽는 스키마 + 형식이 틀린 줄을 통째로 담을 ``_corrupt`` 칸."""
    from pyspark.sql.types import StringType, StructField, StructType

    fields = [StructField(name, StringType()) for name in (*columns, "_corrupt")]
    return StructType(fields)


def _read_strings(spark: Any, csv_paths: Sequence[str], columns: Sequence[str]) -> Any:
    """[쓰임] CSV 들을 글자 그대로 읽고, 줄마다 어느 파일에서 왔는지(``csv_name``)를 붙인다.
    [이유] 형식이 틀린 줄을 Spark 가 조용히 버리지 않도록 PERMISSIVE + ``_corrupt`` 로 받는다."""
    from pyspark.sql import functions as F

    return (
        spark.read.schema(_strings_schema(columns))
        .option("header", "false")
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", "_corrupt")
        .csv(list(csv_paths))
        .withColumn("csv_name", F.element_at(F.split(F.col("_metadata.file_path"), "/"), -1))
    )


def _sources_frame(spark: Any, plan: SourcePlan, csv_name_by_path: dict[str, str]) -> Any:
    """[쓰임] 출처 파일 표 — CSV 이름 → 원천 경로 · 단위 · 기간 시작/끝."""
    rows = []
    for source in plan.sources:
        file = source.file
        rows.append(
            (
                csv_name_by_path[file.relative_path],
                file.relative_path,
                file.granularity.value,
                file.period,
                file.period if file.granularity is Granularity.DAILY else _month_end(plan.month),
                source.sha256,
            )
        )
    return spark.createDataFrame(
        rows,
        "csv_name string, source_path string, granularity string, period_start date,"
        " period_end date, source_sha256 string",
    )


def _month_end(month: date) -> date:
    """[쓰임] 그 달 마지막 날."""
    from datetime import timedelta

    return date(month.year + month.month // 12, month.month % 12 + 1, 1) - timedelta(days=1)


def _typed_trades(raw: Any) -> Any:
    """[쓰임] 글자 칸 → 타입 칸. 바꾸지 못한 칸은 NULL 이 되고 ``parsed`` 가 거짓이 된다.
    [이유] ``try_cast`` 는 실패해도 멈추지 않는다 — 한 줄 때문에 작업 전체가 죽지 않고, 그 줄은
           격리된다."""
    from pyspark.sql import functions as F

    raw_time = F.expr("try_cast(transact_time AS bigint)")
    typed = raw.select(
        "csv_name",
        F.col("agg_trade_id").alias("raw_agg_trade_id"),
        F.concat_ws(",", *[F.col(name) for name in RAW_COLUMNS]).alias("raw_line"),
        (F.col("agg_trade_id") == "agg_trade_id").alias("is_header"),
        F.col("_corrupt").isNotNull().alias("bad_shape"),
        F.expr("try_cast(agg_trade_id AS bigint)").alias("agg_trade_id"),
        F.expr(f"try_cast(price AS {DECIMAL})").alias("price"),
        F.expr(f"try_cast(quantity AS {DECIMAL})").alias("quantity"),
        F.expr("try_cast(first_trade_id AS bigint)").alias("first_trade_id"),
        F.expr("try_cast(last_trade_id AS bigint)").alias("last_trade_id"),
        F.when(raw_time > MICROSECOND_THRESHOLD, F.expr("transact_time div 1000"))
        .otherwise(raw_time)
        .alias("transact_time_ms"),
        F.when(raw_time > MICROSECOND_THRESHOLD, F.lit("us"))
        .otherwise(F.lit("ms"))
        .alias("time_unit"),
        F.when(F.lower("is_buyer_maker") == "true", F.lit(True))
        .when(F.lower("is_buyer_maker") == "false", F.lit(False))
        .alias("is_buyer_maker"),
    )
    required = [
        "agg_trade_id",
        "price",
        "quantity",
        "first_trade_id",
        "last_trade_id",
        "transact_time_ms",
        "is_buyer_maker",
    ]
    parsed = ~F.col("is_header") & ~F.col("bad_shape")
    for name in required:
        parsed = parsed & F.col(name).isNotNull()
    return typed.withColumn("parsed", parsed).withColumn(
        "trade_date", F.to_date(F.timestamp_millis("transact_time_ms"))
    )


def _gaps(parsed: Any) -> tuple[Any, Any]:
    """[쓰임] 파일마다 번호 순으로 이웃한 두 체결을 비교해 빈 곳(빠진 번호)과 시각 역행을 찾는다.
              돌려주는 것: (빈 곳 · 역행 위치 표, 파일마다 역행 수).
    [이유] 번호 전체를 한 줄로 정렬하면 스레드 하나가 수천만 줄을 혼자 정렬한다. 번호를 100 만 단위
           칸(``ID_BUCKET``)으로 나눠 칸 안에서 이웃을 보고(여러 스레드), 칸 경계는 칸마다 첫 · 끝
           체결만 모은 작은 표에서 따로 본다 — 결과는 한 줄로 정렬한 것과 같다."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    keyed = parsed.select(
        "source_path",
        "agg_trade_id",
        "transact_time_ms",
        F.floor(F.col("agg_trade_id") / ID_BUCKET).alias("bucket"),
    )
    # 1 단계: 칸 안 — 바로 앞 체결과 비교.
    within = Window.partitionBy("source_path", "bucket").orderBy("agg_trade_id", "transact_time_ms")
    inner = keyed.select(
        "source_path",
        F.lag("agg_trade_id").over(within).alias("prev_id"),
        F.col("agg_trade_id").alias("next_id"),
        F.lag("transact_time_ms").over(within).alias("prev_ms"),
        F.col("transact_time_ms").alias("next_ms"),
    ).where(F.col("prev_id").isNotNull())
    # 2 단계: 칸 경계 — 칸마다 (첫 번호, 그 시각) · (끝 번호, 그 시각)만 모아 앞 칸의 끝과 비교.
    edges = keyed.groupBy("source_path", "bucket").agg(
        F.min(F.struct("agg_trade_id", "transact_time_ms")).alias("first"),
        F.max(F.struct("agg_trade_id", "transact_time_ms")).alias("last"),
    )
    across = Window.partitionBy("source_path").orderBy("bucket")
    boundary = edges.select(
        "source_path",
        F.lag("last.agg_trade_id").over(across).alias("prev_id"),
        F.col("first.agg_trade_id").alias("next_id"),
        F.lag("last.transact_time_ms").over(across).alias("prev_ms"),
        F.col("first.transact_time_ms").alias("next_ms"),
    ).where(F.col("prev_id").isNotNull())
    pairs = inner.unionByName(boundary)
    # 3 단계: 빠진 번호 수(> 0 이면 빈 곳, 같은 번호면 -1 = 중복) · 시각 역행.
    pairs = pairs.withColumn("missing_ids", F.col("next_id") - F.col("prev_id") - 1).withColumn(
        "time_reversed", F.col("next_ms") < F.col("prev_ms")
    )
    gaps = pairs.where((F.col("missing_ids") > 0) | F.col("time_reversed")).withColumn(
        "kind", F.when(F.col("missing_ids") > 0, F.lit("gap")).otherwise(F.lit("time_reversed"))
    )
    reversed_counts = pairs.groupBy("source_path").agg(
        F.sum(F.col("time_reversed").cast("long")).alias("time_reversed_rows")
    )
    return gaps, reversed_counts


def _klines_daily(spark: Any, kline_csv_paths: Sequence[str]) -> Any | None:
    """[쓰임] 1 분봉 CSV → 날마다 공식 거래량 · taker 매수량 합계. 1 분봉이 없으면 None."""
    from pyspark.sql import functions as F

    if not kline_csv_paths:
        return None
    raw = _read_strings(spark, kline_csv_paths, KLINE_COLUMNS)
    open_ms = F.expr("try_cast(open_time AS bigint)")
    bars = raw.select(
        open_ms.alias("open_ms"),
        F.expr(f"try_cast(volume AS {DECIMAL})").alias("volume"),
        F.expr(f"try_cast(taker_buy_volume AS {DECIMAL})").alias("taker_buy_volume"),
    ).where(F.col("open_ms").isNotNull())
    return (
        bars.withColumn("trade_date", F.to_date(F.timestamp_millis("open_ms")))
        .groupBy("trade_date")
        .agg(
            F.sum("volume").alias("kline_volume"),
            F.sum("taker_buy_volume").alias("kline_taker_buy_volume"),
            F.count("*").alias("kline_bars"),
        )
    )


def build_outputs(
    spark: Any,
    plan: SourcePlan,
    csv_name_by_path: dict[str, str],
    trade_csv_paths: Sequence[str],
    kline_csv_paths: Sequence[str],
    run_id: str,
    materialize: Callable[[Any, str], Any] | None = None,
) -> SilverOutputs:
    """[쓰임] 한 달치 Silver 결과를 만든다(쓰지는 않음). 흐름은 파일 머리말 1 ~ 5 단계.
              ``materialize(df, 이름)`` 를 주면 형식을 맞춘 줄과 깨끗한 체결을 한 번 디스크에 써
              두고 그것을 다시 읽어 쓴다.
    [이유] 만들기와 쓰기를 나눠, 시험에서는 작은 CSV 로 만든 결과만 바로 확인한다. Spark 는 결과를
           쓸 때마다 처음부터 다시 계산(지연 실행)하므로, 큰 달은 무거운 앞 단계(CSV 읽기 · 형식
           맞추기 · 중복 정리)를 한 번만 하도록 중간 결과를 Parquet 으로 남긴다 — 첫 판은 이게 없어
           같은 6,651 만 줄을 표마다 다시 읽었다(65 분)."""
    from pyspark.sql import functions as F

    keep = materialize or (lambda df, _name: df)

    symbol = plan.symbol
    month_text = f"{plan.month:%Y-%m}"
    allowed_days = spark.createDataFrame(
        [(s.file.relative_path, day) for s in plan.sources for day in s.days],
        "source_path string, trade_date date",
    )

    # 1 · 2 단계: 읽고 형식 맞추고 출처를 붙인다.
    rows = keep(
        _typed_trades(_read_strings(spark, trade_csv_paths, RAW_COLUMNS)).join(
            F.broadcast(_sources_frame(spark, plan, csv_name_by_path)), "csv_name"
        ),
        "rows",
    )
    in_period = F.col("trade_date").between(F.col("period_start"), F.col("period_end"))
    data_rows = rows.where(~F.col("is_header"))
    parsed = data_rows.where("parsed")

    # 3 단계: 기간 안 · 그날을 맡은 출처의 줄만 후보로. 일 파일에 넘긴 날은 빼서 센다.
    in_window = parsed.where(in_period)
    candidates = in_window.join(
        F.broadcast(allowed_days), ["source_path", "trade_date"], "left_semi"
    )
    replaced = in_window.join(F.broadcast(allowed_days), ["source_path", "trade_date"], "left_anti")

    # 4 단계: 같은 번호 — 내용까지 같으면 하나만, 내용이 다르면 모두 격리.
    content = F.sha2(
        F.concat_ws(
            "|",
            "price",
            "quantity",
            "first_trade_id",
            "last_trade_id",
            "transact_time_ms",
            "is_buyer_maker",
        ),
        256,
    )
    variants = candidates.groupBy("agg_trade_id").agg(F.countDistinct(content).alias("variants"))
    conflict_ids = variants.where("variants > 1").select("agg_trade_id")
    conflicting = candidates.join(conflict_ids, "agg_trade_id", "left_semi")
    trades = keep(
        candidates.join(conflict_ids, "agg_trade_id", "left_anti")
        .dropDuplicates(["agg_trade_id"])
        .select(
            F.lit(symbol).alias("symbol"),
            "trade_date",
            "agg_trade_id",
            "price",
            "quantity",
            "first_trade_id",
            "last_trade_id",
            "transact_time_ms",
            F.timestamp_millis("transact_time_ms").alias("trade_ts"),
            "is_buyer_maker",
            "source_path",
            F.lit(run_id).alias("run_id"),
        ),
        "trades",
    )

    quarantine = (
        data_rows.where(~F.col("parsed"))
        .withColumn("reason", F.lit("parse_error"))
        .unionByName(parsed.where(~in_period).withColumn("reason", F.lit("outside_period")))
        .unionByName(conflicting.withColumn("reason", F.lit("conflicting_duplicate")))
        .select(
            F.lit(symbol).alias("symbol"),
            F.lit(month_text).alias("period_month"),
            "source_path",
            "reason",
            "raw_line",
            "agg_trade_id",
            "transact_time_ms",
            F.lit(run_id).alias("run_id"),
        )
    )

    # 5 단계: 감사 — 파일마다.
    gaps, reversed_counts = _gaps(parsed)
    gap_totals = (
        gaps.where("kind = 'gap'")
        .groupBy("source_path")
        .agg(F.count("*").alias("gap_count"), F.sum("missing_ids").alias("missing_ids"))
    )
    replaced_counts = replaced.groupBy("source_path").agg(F.count("*").alias("replaced_rows"))
    quarantine_counts = (
        quarantine.groupBy("source_path")
        .pivot("reason", ["parse_error", "outside_period", "conflicting_duplicate"])
        .count()
    )
    header_counts = (
        rows.where("is_header").groupBy("source_path").agg(F.count("*").alias("header_rows"))
    )
    per_file = parsed.groupBy("source_path", "source_sha256", "granularity").agg(
        F.count("*").alias("rows"),
        F.countDistinct("agg_trade_id").alias("distinct_ids"),
        F.min("agg_trade_id").alias("min_id"),
        F.max("agg_trade_id").alias("max_id"),
        F.min("transact_time_ms").alias("min_ms"),
        F.max("transact_time_ms").alias("max_ms"),
        F.array_sort(F.collect_set("time_unit")).alias("time_units"),
        F.sum("quantity").alias("quantity_sum"),
        F.sum(F.when(~F.col("is_buyer_maker"), F.col("quantity"))).alias("taker_buy_quantity_sum"),
    )
    audit_file = (
        per_file.join(header_counts, "source_path", "left")
        .join(gap_totals, "source_path", "left")
        .join(reversed_counts, "source_path", "left")
        .join(replaced_counts, "source_path", "left")
        .join(quarantine_counts, "source_path", "left")
        .fillna(
            0,
            [
                "header_rows",
                "gap_count",
                "missing_ids",
                "time_reversed_rows",
                "replaced_rows",
                "parse_error",
                "outside_period",
                "conflicting_duplicate",
            ],
        )
        .withColumn("duplicate_rows", F.col("rows") - F.col("distinct_ids"))
        .select(
            F.lit(symbol).alias("symbol"),
            F.lit(month_text).alias("period_month"),
            "*",
            F.lit(run_id).alias("run_id"),
        )
    )
    gaps_out = gaps.select(
        F.lit(symbol).alias("symbol"),
        F.lit(month_text).alias("period_month"),
        "source_path",
        "kind",
        "prev_id",
        "next_id",
        "prev_ms",
        "next_ms",
        "missing_ids",
        F.lit(run_id).alias("run_id"),
    )

    # 5 단계: 감사 — 날마다(깨끗한 체결 기준) + 공식 1 분봉 합계와 대조.
    daily = (
        trades.groupBy("symbol", "trade_date")
        .agg(
            F.first("source_path").alias("source_path"),
            F.count("*").alias("rows"),
            F.min("agg_trade_id").alias("min_id"),
            F.max("agg_trade_id").alias("max_id"),
            F.sum("quantity").alias("quantity_sum"),
            F.sum(F.when(~F.col("is_buyer_maker"), F.col("quantity"))).alias(
                "taker_buy_quantity_sum"
            ),
        )
        .withColumn("missing_ids_inside_day", F.col("max_id") - F.col("min_id") + 1 - F.col("rows"))
    )
    klines = _klines_daily(spark, kline_csv_paths)
    if klines is not None:
        daily = daily.join(klines, "trade_date", "left")
    else:
        daily = (
            daily.withColumn("kline_volume", F.lit(None).cast(DECIMAL))
            .withColumn("kline_taker_buy_volume", F.lit(None).cast(DECIMAL))
            .withColumn("kline_bars", F.lit(None).cast("long"))
        )
    diff_pct = F.abs(F.col("quantity_sum") - F.col("kline_volume")) / F.col("kline_volume") * 100
    audit_daily = (
        daily.withColumn("volume_diff_pct", diff_pct.cast("double"))
        .withColumn(
            "suspect",
            (F.col("missing_ids_inside_day") != 0)
            | F.col("kline_volume").isNull()
            | (F.col("volume_diff_pct") > VOLUME_TOLERANCE_PCT),
        )
        .withColumn("period_month", F.lit(month_text))
        .withColumn("run_id", F.lit(run_id))
    )

    # 연구 216 비교용: 파일마다 매시 00 분 [거래량, 공격 매수량, 체결 수].
    minute = F.col("transact_time_ms") - F.col("transact_time_ms") % MINUTE_MS
    check_minutes = (
        parsed.withColumn("minute_ms", minute)
        .where(F.col("minute_ms") % HOUR_MS == 0)
        .groupBy("source_path", "minute_ms")
        .agg(
            F.sum("quantity").alias("quantity"),
            F.sum(F.when(~F.col("is_buyer_maker"), F.col("quantity")).otherwise(0)).alias(
                "taker_buy_quantity"
            ),
            F.count("*").alias("trades"),
        )
    )
    return SilverOutputs(trades, quarantine, audit_file, gaps_out, audit_daily, check_minutes)


def _write_month(df: Any, name: str, predicate: str, partition_by: Sequence[str]) -> int:
    """[쓰임] Delta 표에 그 달 칸만 바꿔 끼우고(replaceWhere) 새 표 버전을 돌려준다.
    [이유] 같은 달을 다시 만들면 그 달만 새 내용으로 바뀌고 다른 달은 그대로다(멱등 · 재처리). 이전
           내용은 Delta 이력에 남아 시간여행으로 다시 볼 수 있다."""
    from delta.tables import DeltaTable

    path = table_path("silver", name)
    spark = df.sparkSession
    writer = df.write.format("delta").partitionBy(*partition_by)
    if DeltaTable.isDeltaTable(spark, path):
        writer.mode("overwrite").option("replaceWhere", predicate).save(path)
    else:
        writer.mode("overwrite").save(path)
    return int(DeltaTable.forPath(spark, path).history(1).collect()[0]["version"])


def write_outputs(outputs: SilverOutputs, symbol: str, month: date) -> dict[str, tuple[int, int]]:
    """[쓰임] 다섯 표를 쓴다. 표 이름 → (Delta 버전, 그 달 행 수).
    [이유] 행 수는 쓴 뒤 그 달 칸을 다시 세어 실행 기록에 남긴다(쓴 것을 확인)."""
    month_text = f"{month:%Y-%m}"
    by_date = (
        f"symbol = '{symbol}' AND trade_date >= '{month:%Y-%m-%d}'"
        f" AND trade_date <= '{_month_end(month):%Y-%m-%d}'"
    )
    by_month = f"symbol = '{symbol}' AND period_month = '{month_text}'"
    plans = [
        ("agg_trades", outputs.trades, by_date, ("symbol", "trade_date")),
        ("agg_trades_quarantine", outputs.quarantine, by_month, ("symbol", "period_month")),
        ("agg_trades_audit_file", outputs.audit_file, by_month, ("symbol", "period_month")),
        ("agg_trades_gaps", outputs.gaps, by_month, ("symbol", "period_month")),
        ("agg_trades_audit_daily", outputs.audit_daily, by_date, ("symbol", "trade_date")),
    ]
    written: dict[str, tuple[int, int]] = {}
    for name, df, predicate, partition_by in plans:
        version = _write_month(df, name, predicate, partition_by)
        spark = df.sparkSession
        rows = spark.read.format("delta").load(table_path("silver", name)).where(predicate).count()
        written[name] = (version, int(rows))
    return written


def _write_report(outputs: SilverOutputs, symbol: str, month: date) -> Path:
    """[쓰임] 연구 216 추출과 비교할 작은 JSON — 파일 감사 · 빈 곳 · 매시 00 분 값.
    [이유] 비교 스크립트(호스트, Spark 없음)가 Delta 를 읽지 않고도 비교할 수 있게. 감사 · 빈 곳은
           방금 쓴 Delta 표에서 그 달만 읽는다(다시 계산하지 않음)."""
    spark = outputs.trades.sparkSession
    by_month = f"symbol = '{symbol}' AND period_month = '{month:%Y-%m}'"

    def rows(df: Any) -> list[dict[str, Any]]:
        return [row.asDict(recursive=True) for row in df.collect()]

    def written(name: str) -> Any:
        return spark.read.format("delta").load(table_path("silver", name)).where(by_month)

    report = {
        "symbol": symbol,
        "month": f"{month:%Y-%m}",
        "audit_file": rows(written("agg_trades_audit_file")),
        "gaps": rows(written("agg_trades_gaps")),
        "check_minutes": rows(outputs.check_minutes),
    }
    path = lake_root() / "silver" / "_reports" / "agg_trades" / f"{symbol}-{month:%Y-%m}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def run_silver(
    symbol: str,
    month: date,
    *,
    bronze_root: Path,
    manifest_path: Path,
    staging_root: Path,
    recorder: RunRecorder,
) -> dict[str, tuple[int, int]]:
    """[쓰임] 한 달치 Silver 를 처음부터 끝까지 — 출처 정하기 → 검증 · 풀기 → 만들기 → 쓰기 → 보고.
    [이유] 실행 기록에 표마다 Delta 버전을 남겨, 나중에 '이 실행이 만든 표' 를 시간여행으로 연다."""
    params = {"symbol": symbol, "month": f"{month:%Y-%m}", "bronze_root": str(bronze_root)}
    manifest = read_manifest(manifest_path)
    with record_run(recorder, "silver.agg_trades", params) as run:
        # 1 단계: 출처 정하기 — 체크섬 목록에 있는 것만.
        plan = plan_month_sources(manifest, symbol, month)
        if not plan.sources:
            raise FileNotFoundError(
                f"Bronze 에 {symbol} {month:%Y-%m} 체결이 없다 (체크섬 목록 기준)"
            )
        kline_plan = plan_month_sources(manifest, symbol, month, Dataset.KLINES, "1m")
        log_json(
            event="sources",
            trades=[s.file.relative_path for s in plan.sources],
            klines=[s.file.relative_path for s in kline_plan.sources],
        )

        # 2 단계: Bronze 검증 → 임시 폴더로 풀기.
        staging = staging_root / str(run.run_id)
        csv_name_by_path: dict[str, str] = {}
        trade_csvs: list[str] = []
        kline_csvs: list[str] = []
        try:
            for source in (*plan.sources, *kline_plan.sources):
                zip_path = bronze_root.joinpath(*source.file.relative_path.split("/"))
                verify_bronze_file(zip_path, source.sha256)
                csv_path = stage_csv(zip_path, staging)
                csv_name_by_path[source.file.relative_path] = csv_path.name
                (kline_csvs if source.file.dataset is Dataset.KLINES else trade_csvs).append(
                    str(csv_path)
                )

            # 3 단계: 만들고 쓴다.
            spark = spark_session("silver.agg_trades")
            spark.sparkContext.setLogLevel("ERROR")

            def to_parquet(df: Any, name: str) -> Any:
                """[쓰임] 중간 결과를 임시 폴더에 Parquet 으로 한 번 쓰고 다시 읽는다."""
                path = str(staging / f"{name}.parquet")
                df.write.mode("overwrite").parquet(path)
                log_json(event="materialized", name=name)
                return spark.read.parquet(path)

            outputs = build_outputs(
                spark,
                plan,
                csv_name_by_path,
                trade_csvs,
                kline_csvs,
                str(run.run_id),
                materialize=to_parquet,
            )
            written = write_outputs(outputs, symbol, month)
            report_path = _write_report(outputs, symbol, month)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        for name, (version, rows) in written.items():
            run.add(
                Artifact(
                    f"silver/{name}",
                    "silver",
                    "replaced_month",
                    row_count=rows,
                    table_version=version,
                )
            )
            log_json(event="table", table=f"silver/{name}", version=version, rows=rows)
        log_json(event="report", path=str(report_path), run_id=str(run.run_id))
    return written


def _parse_month(text: str) -> date:
    """[쓰임] ``YYYY-MM`` → 그 달 1 일."""
    year_text, month_text = text.split("-")
    return date(int(year_text), int(month_text), 1)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(
        prog="python -m mdp.silver.agg_trades", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, type=_parse_month, help="YYYY-MM")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    staging_root = Path(os.environ.get("MDP_STAGING_ROOT", "/tmp/mdp-staging"))

    def run(recorder: RunRecorder) -> None:
        run_silver(
            args.symbol,
            args.month,
            bronze_root=default_bronze_root(),
            manifest_path=args.manifest,
            staging_root=staging_root,
            recorder=recorder,
        )

    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run(recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        run(NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
