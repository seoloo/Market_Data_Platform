"""국내 주식 스트림 1분봉 — Kafka 체결 원문을 Spark Structured Streaming 으로 묶어 확정된 봉만 쓴다.

언제 쓰나: Spark 이미지 안에서 (Kafka 를 켠 뒤)::

    # 토픽에 쌓인 것을 끝까지 처리하고 멈춤 (장 마감 뒤 매일 · 시험)
    docker compose --profile spark run --rm spark \\
        python -m mdp.stream.kis_bars --trigger available-now
    # 장 중에 계속 돌기 (10 초마다, --duration-s 뒤 멈춤)
    docker compose --profile spark run --rm spark python -m mdp.stream.kis_bars --trigger continuous

읽고 쓰는 것:
- 읽기: Kafka 토픽 :data:`~mdp.stream.kis_produce.RAW_TOPIC` (어디까지 읽었는지는 체크포인트에)
- 쓰기: Delta ``<lake>/gold/kr_stream_bars_1m`` (종목 · 날 · 분마다 한 줄, 같은 분은 덮어씀),
  체크포인트 ``<lake>/_checkpoints/kr_stream_bars_1m/<이름>``, 실행 기록(작업 ``stream.kis_bars``)

처리 순서 (바이낸스 스트림 :mod:`mdp.stream.bars_1m` 과 같은 틀):

1. 원문 글자 ``0|H0STCNT0|건수|…`` → 47 칸씩 끊어 체결 한 건씩
   (칸 번호는 :data:`mdp.kis.realtime.FIELD`).
   건수와 칸 수가 맞지 않는 메시지는 버린다.
2. 체결 시각 = 영업일(``BSOP_DATE``) + 체결 시각(``STCK_CNTG_HOUR``, 서울) → UTC.
3. **워터마크** 뒤 **중복 제거** — 같은 (종목, 누적 거래량)은 한 번만. 체결 번호가 없어서
   누적 거래량을 번호처럼 쓴다(체결마다 그 체결량만큼 늘어난다).
4. **1 분 창 집계** — 시가 · 종가는 (체결 시각, 누적 거래량) 순서로 첫 · 마지막.
   ``acml_contiguous`` = 그 분 안에서 '첫 누적 − 첫 체결량 + 체결량 합 = 마지막 누적' 인가
   (빠진 체결 없음).
5. 워터마크가 그 분을 지나면 봉을 내보내 Delta 에 **MERGE** (정확히 한 번 효과).

[이유] 분 이름은 분봉 조회(K-2)와 같게 '서울 시각 분의 시작'(``bar_time`` ``HHMM00``)으로 둔다 —
       대사(:mod:`mdp.reconcile.kis_daily`)가 (종목, 날, 분)으로 바로 맞댄다. 분봉 조회의 분 이름이
       시작 시각인지는 대사 결과로 확인한다(확인 필요).
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import time
from collections.abc import Sequence
from typing import Any, Final

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.kis.realtime import FIELD, TRADE_COLUMNS, TRADE_TR
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.spark.session import lake_root, spark_session, table_path
from mdp.stream.bars_1m import _dropped_by_watermark, _progress_summary
from mdp.stream.kis_produce import RAW_TOPIC

__all__ = ["STREAM_TABLE", "kis_stream_bars", "main", "parse_kis_raw", "upsert_kis_bars"]

STREAM_TABLE: Final = "kr_stream_bars_1m"
PRICE: Final = "decimal(18,2)"


def parse_kis_raw(raw: Any) -> Any:
    """[쓰임] Kafka 줄(``value`` 바이트 · ``timestamp`` 받은 시각) → 체결 한 건씩.
              칸: 종목 · 영업일 · 체결 시각 글자 · 가격 · 체결량 · 누적 거래량 · 누적 거래대금 ·
              체결 구분 · 장 구분 코드들 · ``event_time`` · ``received_ms``.
    [이유] 숫자는 글자에서 바로 decimal · 정수로(실수를 거치지 않음). 47 칸의 배수가 아니거나
           머리의 건수와 다르면 그 메시지는 통째로 버린다 — 반쪽 체결을 만들지 않는다."""
    from pyspark.sql import functions as F

    width = len(TRADE_COLUMNS)
    parts = F.split(F.col("value").cast("string"), r"\|", 4)
    framed = raw.select(
        parts.getItem(0).alias("flag"),
        parts.getItem(1).alias("tr_id"),
        parts.getItem(2).cast("int").alias("count"),
        F.split(parts.getItem(3), r"\^").alias("fields"),
        F.unix_millis("timestamp").alias("received_ms"),
    ).where(
        (F.col("flag") == "0")
        & (F.col("tr_id") == TRADE_TR)
        & (F.size("fields") % width == 0)
        & (F.size("fields") / width == F.col("count"))
    )
    records = framed.select(
        "received_ms",
        F.explode(
            F.expr(
                f"transform(sequence(0, count - 1), i -> slice(fields, i * {width} + 1, {width}))"
            )
        ).alias("r"),
    )

    def field(name: str) -> Any:
        return F.col("r").getItem(FIELD[name])

    trades = records.select(
        field("MKSC_SHRN_ISCD").alias("symbol"),
        F.to_date(field("BSOP_DATE"), "yyyyMMdd").alias("trade_date"),
        field("STCK_CNTG_HOUR").alias("trade_time"),
        F.expr(f"try_cast(r[{FIELD['STCK_PRPR']}] AS {PRICE})").alias("price"),
        F.expr(f"try_cast(r[{FIELD['CNTG_VOL']}] AS BIGINT)").alias("trade_volume"),
        F.expr(f"try_cast(r[{FIELD['ACML_VOL']}] AS BIGINT)").alias("cumulative_volume"),
        F.expr(f"try_cast(r[{FIELD['ACML_TR_PBMN']}] AS DECIMAL(28,0))").alias("cumulative_value"),
        field("CCLD_DVSN").alias("side_code"),
        field("NEW_MKOP_CLS_CODE").alias("market_op_code"),
        field("HOUR_CLS_CODE").alias("hour_code"),
        "received_ms",
    ).where(
        "symbol IS NOT NULL AND trade_date IS NOT NULL AND price IS NOT NULL"
        " AND trade_volume IS NOT NULL AND cumulative_volume IS NOT NULL"
    )
    local = F.to_timestamp(
        F.concat(F.date_format("trade_date", "yyyy-MM-dd"), F.lit(" "), "trade_time"),
        "yyyy-MM-dd HHmmss",
    )
    return trades.withColumn("event_time", F.to_utc_timestamp(local, "Asia/Seoul"))


def kis_stream_bars(trades: Any, watermark: str) -> Any:
    """[쓰임] 체결 흐름 → 1 분봉 흐름. 칸: 종목 · ``trade_date`` · ``bar_time`` · ``bar_start`` ·
              시고저종 · ``volume`` · ``trade_count`` · 첫 · 마지막 누적 거래량 · 첫 체결량 ·
              ``acml_contiguous`` ·
              ``last_received_ms``.
    [이유] 중복 제거 키에 체결 시각을 넣는다 — 워터마크가 지난 시각의 상태를 Spark 가 지울 수 있게
           (바이낸스 스트림과 같은 까닭). 같은 체결은 시각도 같으므로 결과는 누적 거래량으로만 거른
           것과 같다."""
    from pyspark.sql import functions as F

    order = F.struct("event_time", "cumulative_volume")
    window = F.window("event_time", "1 minute")
    bars = (
        trades.withWatermark("event_time", watermark)
        .dropDuplicates(["symbol", "cumulative_volume", "event_time"])
        .groupBy("symbol", window.alias("w"))
        .agg(
            F.min_by("price", order).alias("open"),
            F.max("price").alias("high"),
            F.min("price").alias("low"),
            F.max_by("price", order).alias("close"),
            F.sum("trade_volume").alias("volume"),
            F.count(F.lit(1)).alias("trade_count"),
            F.min("cumulative_volume").alias("first_cumulative"),
            F.max("cumulative_volume").alias("last_cumulative"),
            F.min_by("trade_volume", order).alias("first_trade_volume"),
            F.max("received_ms").alias("last_received_ms"),
        )
    )
    local = F.from_utc_timestamp("w.start", "Asia/Seoul")
    return bars.select(
        "symbol",
        F.to_date(local).alias("trade_date"),
        F.date_format(local, "HHmmss").alias("bar_time"),
        F.col("w.start").alias("bar_start"),
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "first_cumulative",
        "last_cumulative",
        "first_trade_volume",
        (
            F.col("first_cumulative") - F.col("first_trade_volume") + F.col("volume")
            == F.col("last_cumulative")
        ).alias("acml_contiguous"),
        "last_received_ms",
    )


def upsert_kis_bars(batch: Any, batch_id: int, run_id: str) -> int:
    """[쓰임] 묶음 하나의 확정된 봉을 Delta 표에 MERGE — (종목, 날, 분)이 같으면 덮어쓴다. 쓴 줄 수.
    [이유] Spark 가 실패 뒤 같은 묶음을 다시 써도 결과가 같다(정확히 한 번 효과)."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F

    rows = batch.withColumn("batch_id", F.lit(batch_id).cast("long")).withColumn(
        "run_id", F.lit(run_id)
    )
    count = int(rows.count())
    if count == 0:
        return 0
    spark = batch.sparkSession
    path = table_path("gold", STREAM_TABLE)
    if not DeltaTable.isDeltaTable(spark, path):
        rows.write.format("delta").partitionBy("trade_date").save(path)
        return count
    (
        DeltaTable.forPath(spark, path)
        .alias("t")
        .merge(
            rows.alias("s"),
            "t.trade_date = s.trade_date AND t.symbol = s.symbol AND t.bar_time = s.bar_time",
        )
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )
    return count


def run_stream(args: argparse.Namespace, recorder: RunRecorder) -> dict[str, int]:
    """[쓰임] 스트림 질의를 띄우고, 멈추면 처리한 양 · 늦어서 버린 양 · 표 버전을 기록한다."""
    from delta.tables import DeltaTable

    params: dict[str, object] = {
        "topic": args.topic,
        "trigger": args.trigger,
        "watermark": args.watermark,
        "checkpoint": args.checkpoint_name,
    }
    checkpoint = lake_root() / "_checkpoints" / STREAM_TABLE / args.checkpoint_name
    if args.fresh:
        shutil.rmtree(checkpoint, ignore_errors=True)
    with record_run(recorder, "stream.kis_bars", params) as handle:
        spark = spark_session("stream.kis_bars")
        spark.sparkContext.setLogLevel("ERROR")
        path = table_path("gold", STREAM_TABLE)
        if args.fresh and DeltaTable.isDeltaTable(spark, path):
            DeltaTable.forPath(
                spark, path
            ).delete()  # 원문 처음부터 — 지운 것은 Delta 이력에 남는다
        spark.conf.set(
            "spark.sql.streaming.stateStore.providerClass",
            "org.apache.spark.sql.execution.streaming.state.RocksDBStateStoreProvider",
        )
        spark.conf.set("spark.sql.streaming.numRecentProgressUpdates", "100000")
        raw = (
            spark.readStream.format("kafka")
            .option("kafka.bootstrap.servers", args.bootstrap)
            .option("subscribe", args.topic)
            .option("startingOffsets", "earliest")
            .option("maxOffsetsPerTrigger", args.max_offsets)
            .load()
        )
        run_id = str(handle.run_id)
        written = {"bars": 0}

        def sink(batch: Any, batch_id: int) -> None:
            written["bars"] += upsert_kis_bars(batch, batch_id, run_id)

        writer = (
            kis_stream_bars(parse_kis_raw(raw), args.watermark)
            .writeStream.outputMode("append")
            .foreachBatch(sink)
            .option("checkpointLocation", str(checkpoint))
            .queryName(STREAM_TABLE)
        )
        if args.trigger == "available-now":
            query = writer.trigger(availableNow=True).start()
            query.awaitTermination()
        else:
            query = writer.trigger(processingTime=args.processing_time).start()
            deadline = time.monotonic() + args.duration_s
            while query.isActive and time.monotonic() < deadline:
                time.sleep(5)
                if query.lastProgress:
                    log_json(event="progress", **_progress_summary(query.lastProgress))
            query.stop()
        if query.exception() is not None:
            raise RuntimeError(f"스트림 질의 실패: {query.exception()}")
        progress = list(query.recentProgress)
        totals = {
            "batches": len(progress),
            "input_rows": sum(int(p.get("numInputRows", 0)) for p in progress),
            "dropped_by_watermark": sum(_dropped_by_watermark(p) for p in progress),
            "bars_written": written["bars"],
        }
        if DeltaTable.isDeltaTable(spark, path):
            version = int(DeltaTable.forPath(spark, path).history(1).collect()[0]["version"])
            table_rows = spark.read.format("delta").load(path).count()
            handle.add(
                Artifact(
                    f"gold/{STREAM_TABLE}",
                    "stream",
                    "upserted",
                    row_count=table_rows,
                    table_version=version,
                )
            )
            totals |= {"table_version": version, "table_rows": table_rows}
        log_json(event="summary", run_id=run_id, **totals)
    return totals


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    logging.getLogger("py4j").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m mdp.stream.kis_bars")
    parser.add_argument(
        "--trigger", choices=["available-now", "continuous"], default="available-now"
    )
    parser.add_argument("--processing-time", default="10 seconds")
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--watermark", default="2 minutes", help="늦게 온 체결을 기다리는 시간")
    parser.add_argument("--max-offsets", type=int, default=200_000)
    parser.add_argument("--topic", default=RAW_TOPIC)
    parser.add_argument("--checkpoint-name", default="default")
    parser.add_argument(
        "--fresh", action="store_true", help="체크포인트 · 표를 지우고 원문 처음부터"
    )
    parser.add_argument("--bootstrap", default=os.environ.get("MDP_KAFKA_BOOTSTRAP", "kafka:9092"))
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run_stream(args, recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        run_stream(args, NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
