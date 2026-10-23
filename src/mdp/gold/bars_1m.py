"""Gold 1 분봉 — Silver 깨끗한 체결을 1 분 단위로 묶고, 바이낸스 공식 1 분봉과 맞대 본다.

언제 쓰나: Silver 체결을 만든 뒤, Spark 이미지 안에서::

    docker compose run --rm spark python -m mdp.gold.bars_1m --symbol BTCUSDT --month 2024-03

읽고 쓰는 것:
- 읽기: ``<lake>/silver/agg_trades`` (그 달) · 체크섬 목록 · Bronze 공식 1 분봉 zip (있으면, 대조용)
- 쓰기 (``<lake>/gold/``, Delta, 그 달 칸만 바꿔 끼움):
  ``bars_1m`` (분마다 한 줄) · ``bars_1m_kline_check`` (날마다 공식 봉과 맞은 정도) ·
  실행 기록(작업 ``gold.bars_1m``, 표마다 Delta 버전 · 읽은 Silver 버전)

봉 한 줄 (``bars_1m``):
- 시가 · 종가 = 그 분의 첫 · 마지막 체결 가격 (순서는 체결 시각, 같으면 체결 번호)
- 고가 · 저가 = 최고 · 최저 가격
- 거래량 · 거래대금(가격 × 수량) · 공격 매수량 · 공격 매수 대금 (decimal 그대로 더함)
- ``trade_count`` = 개별 체결 수(묶음 하나 = 끝 번호 − 첫 번호 + 1, 공식 봉의 ``count`` 와 같은 뜻),
  ``agg_trade_count`` = 묶음(aggTrade) 수
- 체결이 없는 분은 줄이 없다(빈 봉을 만들지 않는다 — 없음을 0 으로 꾸미지 않으려고)

[이유] Gold 는 Silver 에서만 만든다 — Bronze 를 다시 읽지 않으므로 Silver 의 정제 · 격리 규칙이
       그대로 이어지고, Silver 를 고쳐 다시 만들면 Gold 도 같은 명령으로 다시 만들어진다. 어느
       Silver 버전에서 만들었는지 (``silver_version``)를 줄마다 남겨 계보(lineage)를 추적한다.
"""

from __future__ import annotations

import argparse
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

from mdp.bronze.archive import Dataset
from mdp.bronze.fetch import (
    DEFAULT_MANIFEST,
    default_bronze_root,
    log_json,
    read_manifest,
    setup_cli_logging,
)
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.silver.sources import plan_month_sources, stage_csv, verify_bronze_file
from mdp.spark.csv import KLINE_COLUMNS, read_strings
from mdp.spark.session import spark_session, table_path

__all__ = [
    "BAR_COLUMNS",
    "GoldOutputs",
    "bar_aggregations",
    "build_bars",
    "check_against_klines",
    "main",
    "run_gold",
]

MINUTE_MS: Final = 60_000
DECIMAL: Final = "decimal(18,8)"


@dataclass(frozen=True, slots=True)
class GoldOutputs:
    """[쓰임] 한 달치 Gold 결과 DataFrame 두 개 (아직 쓰기 전)."""

    bars: Any
    kline_check: Any | None


def _month_bounds(month: date) -> tuple[date, date]:
    """[쓰임] 그 달 첫날과 다음 달 첫날."""
    return month, date(month.year + month.month // 12, month.month % 12 + 1, 1)


def _month_predicate(symbol: str, month: date) -> str:
    """[쓰임] Delta 칸 고르기 조건 — 그 종목 · 그 달 (``trade_date`` 파티션)."""
    first, next_first = _month_bounds(month)
    last = next_first - timedelta(days=1)
    return (
        f"symbol = '{symbol}' AND trade_date >= '{first:%Y-%m-%d}'"
        f" AND trade_date <= '{last:%Y-%m-%d}'"
    )


BAR_COLUMNS: Final = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "trade_count",
    "agg_trade_count",
    "first_agg_trade_id",
    "last_agg_trade_id",
)
"""봉 한 줄의 값 칸 — 배치 봉(``bars_1m``)과 스트림 봉(``stream_bars_1m``)이 같은 칸을
같은 식으로."""


def bar_aggregations() -> list[Any]:
    """[쓰임] 체결 묶음 → 봉 값 칸(:data:`BAR_COLUMNS`)을 만드는 집계식 목록.
    [이유] 배치(:func:`build_bars`)와 스트림(:mod:`mdp.stream.bars_1m`)이 **같은 함수**를 쓴다 —
           두 봉을 맞대는 대사(5-2)에서 '계산이 달라서' 생기는 차이를 처음부터 없앤다.
           시가 · 종가는 ``min_by`` · ``max_by`` 에 (시각, 번호) 묶음을 기준으로 줘서, 같은 밀리초에
           체결이 여럿이어도 언제나 같은 줄을 고른다(다시 만들어도 같은 봉)."""
    from pyspark.sql import functions as F

    order = F.struct("transact_time_ms", "agg_trade_id")
    notional = F.col("price") * F.col("quantity")
    taker_buy = ~F.col("is_buyer_maker")
    return [
        F.min_by("price", order).alias("open"),
        F.max("price").alias("high"),
        F.min("price").alias("low"),
        F.max_by("price", order).alias("close"),
        F.sum("quantity").alias("volume"),
        F.sum(notional).alias("quote_volume"),
        F.sum(F.when(taker_buy, F.col("quantity")).otherwise(F.lit(0).cast(DECIMAL))).alias(
            "taker_buy_volume"
        ),
        F.sum(F.when(taker_buy, notional).otherwise(F.lit(0))).alias("taker_buy_quote_volume"),
        F.sum(F.col("last_trade_id") - F.col("first_trade_id") + 1).alias("trade_count"),
        F.count("*").alias("agg_trade_count"),
        F.min("agg_trade_id").alias("first_agg_trade_id"),
        F.max("agg_trade_id").alias("last_agg_trade_id"),
    ]


def build_bars(trades: Any, silver_version: int, run_id: str) -> Any:
    """[쓰임] 깨끗한 체결 DataFrame → 1 분봉 DataFrame (분마다 한 줄,
    값은 :func:`bar_aggregations`)."""
    from pyspark.sql import functions as F

    minute = F.col("transact_time_ms") - F.col("transact_time_ms") % MINUTE_MS
    return (
        trades.withColumn("bar_start_ms", minute)
        .groupBy("symbol", "bar_start_ms")
        .agg(*bar_aggregations())
        .withColumn("bar_start", F.timestamp_millis("bar_start_ms"))
        .withColumn("trade_date", F.to_date("bar_start"))
        .withColumn("silver_version", F.lit(silver_version).cast("long"))
        .withColumn("run_id", F.lit(run_id))
    )


def check_against_klines(bars: Any, kline_rows: Any, symbol: str, run_id: str) -> Any:
    """[쓰임] Gold 봉과 공식 1 분봉을 분마다 맞대어, 날마다 '몇 분이 칸마다 정확히 같은가' 를 센다.
    [이유] 공식 봉과 체결로 만든 봉은 분 경계에 걸린 체결을 어느 분에 넣느냐가 달라 모든 분이 같지는
           않다 (연구 216: 1,440 분 중 1,110 분 정확히 일치, 하루 합계는 같음). 그래서 '같아야 한다'
           가 아니라 '얼마나 같은가' 를 기록하고, 하루 합계가 같은지를 따로 본다."""
    from pyspark.sql import functions as F

    official = kline_rows.select(
        F.expr("try_cast(open_time AS bigint)").alias("bar_start_ms"),
        *[
            F.expr(f"try_cast({c} AS {DECIMAL})").alias(f"k_{c}")
            for c in ("open", "high", "low", "close", "volume", "taker_buy_volume")
        ],
        F.expr("try_cast(count AS bigint)").alias("k_count"),
    ).where("bar_start_ms IS NOT NULL")
    joined = bars.join(official, "bar_start_ms", "full_outer").withColumn(
        "day", F.to_date(F.timestamp_millis("bar_start_ms"))
    )
    both = F.col("open").isNotNull() & F.col("k_open").isNotNull()
    same_ohlc = (
        both
        & (F.col("open") == F.col("k_open"))
        & (F.col("high") == F.col("k_high"))
        & (F.col("low") == F.col("k_low"))
        & (F.col("close") == F.col("k_close"))
    )
    same_volume = both & (F.col("volume") == F.col("k_volume"))
    same_count = both & (F.col("trade_count") == F.col("k_count"))
    return (
        joined.groupBy("day")
        .agg(
            F.count(F.when(F.col("open").isNotNull(), 1)).alias("gold_minutes"),
            F.count(F.when(F.col("k_open").isNotNull(), 1)).alias("kline_minutes"),
            F.count(F.when(same_ohlc & same_volume & same_count, 1)).alias("all_fields_equal"),
            F.count(F.when(same_ohlc, 1)).alias("ohlc_equal"),
            F.count(F.when(same_volume, 1)).alias("volume_equal"),
            F.count(F.when(same_count, 1)).alias("trade_count_equal"),
            F.sum("volume").alias("gold_volume"),
            F.sum("k_volume").alias("kline_volume"),
            F.sum("trade_count").alias("gold_trade_count"),
            F.sum("k_count").alias("kline_trade_count"),
        )
        .withColumn("symbol", F.lit(symbol))
        .withColumnRenamed("day", "trade_date")
        .withColumn("run_id", F.lit(run_id))
    )


def _write_month(df: Any, name: str, predicate: str) -> int:
    """[쓰임] Gold Delta 표에 그 달 칸만 바꿔 끼우고 새 버전을 돌려준다(Silver 와 같은 방식)."""
    from delta.tables import DeltaTable

    path = table_path("gold", name)
    spark = df.sparkSession
    writer = df.write.format("delta").partitionBy("symbol", "trade_date")
    if DeltaTable.isDeltaTable(spark, path):
        writer.mode("overwrite").option("replaceWhere", predicate).save(path)
    else:
        writer.mode("overwrite").save(path)
    return int(DeltaTable.forPath(spark, path).history(1).collect()[0]["version"])


def run_gold(
    symbol: str,
    month: date,
    *,
    bronze_root: Path,
    manifest_path: Path,
    staging_root: Path,
    recorder: RunRecorder,
) -> dict[str, tuple[int, int]]:
    """[쓰임] 한 달치 Gold — Silver 읽기 → 1 분봉 → (공식 1 분봉 있으면) 대조 → 쓰기 → 실행 기록."""
    from delta.tables import DeltaTable

    params = {"symbol": symbol, "month": f"{month:%Y-%m}"}
    predicate = _month_predicate(symbol, month)
    with record_run(recorder, "gold.bars_1m", params) as run:
        spark = spark_session("gold.bars_1m")
        spark.sparkContext.setLogLevel("ERROR")
        silver_path = table_path("silver", "agg_trades")
        silver_version = int(
            DeltaTable.forPath(spark, silver_path).history(1).collect()[0]["version"]
        )
        trades = spark.read.format("delta").load(silver_path).where(predicate)
        bars = build_bars(trades, silver_version, str(run.run_id)).cache()
        if bars.limit(1).count() == 0:
            raise FileNotFoundError(f"Silver 에 {symbol} {month:%Y-%m} 체결이 없다")

        # 공식 1 분봉 대조 — Bronze 에 있을 때만(없으면 봉만 쓴다).
        kline_plan = plan_month_sources(
            read_manifest(manifest_path), symbol, month, Dataset.KLINES, "1m"
        )
        staging = staging_root / f"gold-{run.run_id}"
        written: dict[str, tuple[int, int]] = {}
        try:
            kline_check = None
            if kline_plan.sources:
                csvs = []
                for source in kline_plan.sources:
                    zip_path = bronze_root.joinpath(*source.file.relative_path.split("/"))
                    verify_bronze_file(zip_path, source.sha256)
                    csvs.append(str(stage_csv(zip_path, staging)))
                kline_check = check_against_klines(
                    bars, read_strings(spark, csvs, KLINE_COLUMNS), symbol, str(run.run_id)
                )
            written["bars_1m"] = (_write_month(bars, "bars_1m", predicate), bars.count())
            if kline_check is not None:
                kline_check = kline_check.where(predicate)
                written["bars_1m_kline_check"] = (
                    _write_month(kline_check, "bars_1m_kline_check", predicate),
                    kline_check.count(),
                )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        for name, (version, rows) in written.items():
            run.add(
                Artifact(
                    f"gold/{name}",
                    "gold",
                    f"from_silver_v{silver_version}",
                    row_count=rows,
                    table_version=version,
                )
            )
            log_json(
                event="table",
                table=f"gold/{name}",
                version=version,
                rows=rows,
                silver_version=silver_version,
            )
    return written


def _parse_month(text: str) -> date:
    """[쓰임] ``YYYY-MM`` → 그 달 1 일."""
    year_text, month_text = text.split("-")
    return date(int(year_text), int(month_text), 1)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.gold.bars_1m")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, type=_parse_month, help="YYYY-MM")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    staging_root = Path(os.environ.get("MDP_STAGING_ROOT", "/tmp/mdp-staging"))

    def run(recorder: RunRecorder) -> None:
        run_gold(
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
