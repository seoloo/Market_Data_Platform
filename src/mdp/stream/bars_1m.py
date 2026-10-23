"""스트림 1 분봉 — Kafka 체결 원문을 Spark Structured Streaming 으로 묶어, 확정된 봉만 Delta 에
쓴다.

언제 쓰나: Spark 이미지 안에서 (Kafka 를 켠 뒤)::

    # 토픽에 있는 것을 끝까지 처리하고 멈춤 (재생 시연 · 시험)
    docker compose run --rm spark python -m mdp.stream.bars_1m --trigger available-now --fresh
    # 계속 돌기 (10 초마다 새 메시지 처리, --duration-s 뒤 멈춤)
    docker compose run --rm spark python -m mdp.stream.bars_1m --trigger continuous --duration-s 600

읽고 쓰는 것:
- 읽기: Kafka 토픽 :data:`~mdp.stream.messages.RAW_TOPIC` (어디까지 읽었는지는 체크포인트에)
- 쓰기: Delta ``<lake>/gold/stream_bars_1m`` (분마다 한 줄, 같은 분은 덮어씀) ·
  체크포인트 ``<lake>/_checkpoints/stream_bars_1m/<이름>`` · 실행 기록(작업 ``stream.bars_1m``)

처리 순서 (메시지 하나가 봉이 되기까지):
1. 원문 JSON → 체결 칸 (Silver 와 같은 이름 · 같은 decimal). 칸이 빠진 메시지는 버린다.
2. **워터마크** — '지금까지 본 가장 늦은 체결 시각 − ``--watermark``' 보다 이른 체결은 늦게 온
   것으로 보고 버린다. 그래야 Spark 가 '이 분은 더 올 것이 없다' 고 판단해 봉을 확정하고
   상태를 비운다. 늦음 판정은 **바로 앞 묶음의** 워터마크로 한다(상태 연산자가 둘이라) — 실제로
   기다려 주는 시간은 '워터마크 + 묶음 하나' 쯤이다(docs/STREAM.md).
3. **중복 제거** — 같은 (종목, 묶음 번호)는 한 번만 (재연결 · 재전송으로 두 번 온 체결).
4. **1 분 창 집계** — 배치 Gold 와 **같은 집계식** (:func:`mdp.gold.bars_1m.bar_aggregations`).
5. 워터마크가 그 분의 끝을 지나면 봉 한 줄을 내보낸다(append) → Delta 에 **MERGE**
   (같은 분이면 덮어씀).

[이유] 확정된 봉만 쓴다 — 아직 체결이 더 올 수 있는 분을 써 두면 나중에 값이 바뀌어 대사가 흔들린다.
       MERGE 로 쓰는 것은 정확히 한 번(exactly-once) 효과를 위해서다: Spark 는 실패 뒤 같은 묶음
       (micro-batch)을 다시 쓸 수 있는데, 같은 분을 덮어쓰면 다시 써도 결과가 같다(멱등).
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
from mdp.gold.bars_1m import BAR_COLUMNS, DECIMAL, bar_aggregations
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.spark.session import lake_root, spark_session, table_path
from mdp.stream.messages import RAW_TOPIC

__all__ = [
    "STREAM_TABLE",
    "ensure_repair_columns",
    "main",
    "parse_raw",
    "stream_bars",
    "upsert_bars",
]

STREAM_TABLE: Final = "stream_bars_1m"
RAW_SCHEMA: Final = (
    "e string, E long, a long, s string, p string, q string, f long, l long, T long, m boolean"
)
"""원문 JSON 칸 (:mod:`mdp.stream.messages` 의 모양)."""


def parse_raw(raw: Any, keep: Sequence[str] = ()) -> Any:
    """[쓰임] Kafka 줄(``value`` 바이트 · ``timestamp`` 받은 시각) → 체결 칸. ``keep`` 칸은 그대로
              딸려 보낸다(대사가 원천 표시 ``source`` 를 볼 때).
    [이유] 칸 이름 · 타입을 Silver ``agg_trades`` 와 같게 맞춘다 — 같은 집계식을 그대로 쓰려고.
           가격 · 수량은 글자에서 바로 decimal 로(실수를 거치지 않음)."""
    from pyspark.sql import functions as F

    trade = F.from_json(F.col("value").cast("string"), RAW_SCHEMA)
    return (
        raw.select(trade.alias("t"), F.unix_millis("timestamp").alias("received_ms"), *keep)
        .select(
            F.col("t.s").alias("symbol"),
            F.col("t.a").alias("agg_trade_id"),
            F.expr(f"try_cast(t.p AS {DECIMAL})").alias("price"),
            F.expr(f"try_cast(t.q AS {DECIMAL})").alias("quantity"),
            F.col("t.f").alias("first_trade_id"),
            F.col("t.l").alias("last_trade_id"),
            F.col("t.T").alias("transact_time_ms"),
            F.col("t.m").alias("is_buyer_maker"),
            "received_ms",
            *keep,
        )
        .where(
            "symbol IS NOT NULL AND agg_trade_id IS NOT NULL AND price IS NOT NULL"
            " AND quantity IS NOT NULL AND first_trade_id IS NOT NULL"
            " AND last_trade_id IS NOT NULL AND transact_time_ms IS NOT NULL"
            " AND is_buyer_maker IS NOT NULL"
        )
        .withColumn("event_time", F.timestamp_millis("transact_time_ms"))
    )


def stream_bars(trades: Any, watermark: str) -> Any:
    """[쓰임] 체결 흐름 → 1 분봉 흐름 (위 2 ~ 4).
              칸: 종목 · ``bar_start_ms`` · :data:`BAR_COLUMNS` ·
              ``last_received_ms`` (그 분 체결을 마지막으로 받은 시각, 신선도용) ·
              ``ids_contiguous`` (그 분 안 묶음 번호가 빠짐없이 이어지는가).
    [이유] 중복 제거 키에 체결 시각을 넣는다 — 워터마크가 지난 시각의 기억(상태)을 Spark 가 지울 수
           있어야 상태가 끝없이 커지지 않는다. 같은 체결은 시각도 같으므로 결과는 번호로만 거른 것과
           같다."""
    from pyspark.sql import functions as F

    window = F.window("event_time", "1 minute")
    return (
        trades.withWatermark("event_time", watermark)
        .dropDuplicates(["symbol", "agg_trade_id", "event_time"])
        .groupBy("symbol", window.alias("w"))
        .agg(*bar_aggregations(), F.max("received_ms").alias("last_received_ms"))
        .select(
            "symbol",
            F.unix_millis("w.start").alias("bar_start_ms"),
            *BAR_COLUMNS,
            "last_received_ms",
        )
        .withColumn(
            "ids_contiguous",
            F.col("last_agg_trade_id") - F.col("first_agg_trade_id") + 1
            == F.col("agg_trade_count"),
        )
    )


REPAIR_COLUMNS: Final = {"repaired": "boolean", "repair_run_id": "string"}
"""대사(5-2)가 배치 봉으로 바꿔 넣은 분 표시 — 스트림이 쓴 봉은 ``repaired = false``."""


def ensure_repair_columns(spark: Any, path: str) -> None:
    """[쓰임] 표에 :data:`REPAIR_COLUMNS` 가 없으면 더한다(먼저 만든 표를 위해, 한 번만).
    [이유] MERGE 의 '모든 칸 덮어쓰기' 는 양쪽 칸이 같아야 한다 — 스트림과 대사가 같은 칸으로
           쓴다."""
    columns = set(spark.read.format("delta").load(path).columns)
    missing = [f"{name} {kind}" for name, kind in REPAIR_COLUMNS.items() if name not in columns]
    if missing:
        spark.sql(f"ALTER TABLE delta.`{path}` ADD COLUMNS ({', '.join(missing)})")


def upsert_bars(batch: Any, batch_id: int, run_id: str) -> int:
    """[쓰임] 묶음 하나의 확정된 봉을 Delta 표에 MERGE — (종목, 분)이 같으면 덮어쓰고 없으면 넣는다.
              쓴 줄 수를 돌려준다.
    [이유] 위 '정확히 한 번 효과'. 날짜 파티션을 조건에 넣어 그 날짜 칸만 열어 본다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F

    rows = (
        batch.withColumn("bar_start", F.timestamp_millis("bar_start_ms"))
        .withColumn("trade_date", F.to_date("bar_start"))
        .withColumn("batch_id", F.lit(batch_id).cast("long"))
        .withColumn("run_id", F.lit(run_id))
        .withColumn("repaired", F.lit(False))
        .withColumn("repair_run_id", F.lit(None).cast("string"))
    )
    count = int(rows.count())
    if count == 0:
        return 0
    spark = batch.sparkSession
    path = table_path("gold", STREAM_TABLE)
    if not DeltaTable.isDeltaTable(spark, path):
        rows.write.format("delta").partitionBy("symbol", "trade_date").save(path)
        return count
    ensure_repair_columns(spark, path)
    (
        DeltaTable.forPath(spark, path)
        .alias("t")
        .merge(
            rows.alias("s"),
            "t.symbol = s.symbol AND t.trade_date = s.trade_date"
            " AND t.bar_start_ms = s.bar_start_ms",
        )
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )
    return count


def _dropped_by_watermark(progress: dict[str, Any]) -> int:
    """[쓰임] 묶음 진행 정보에서 '워터마크보다 늦어서 버린 줄' 수 (상태 연산자마다 더함)."""
    return sum(
        int(op.get("numRowsDroppedByWatermark", 0)) for op in progress.get("stateOperators", [])
    )


def run_stream(args: argparse.Namespace, recorder: RunRecorder) -> dict[str, int]:
    """[쓰임] 스트림 질의를 띄우고, 멈추면 처리한 양 · 버린 양 · 표 버전을 기록한다."""
    from delta.tables import DeltaTable

    params: dict[str, object] = {
        "topic": args.topic,
        "trigger": args.trigger,
        "watermark": args.watermark,
        "max_offsets": args.max_offsets,
        "checkpoint": args.checkpoint_name,
        "starting_offsets": args.starting_offsets,
    }
    checkpoint = lake_root() / "_checkpoints" / STREAM_TABLE / args.checkpoint_name
    if args.fresh:
        shutil.rmtree(checkpoint, ignore_errors=True)
    with record_run(recorder, "stream.bars_1m", params) as handle:
        spark = spark_session("stream.bars_1m")
        spark.sparkContext.setLogLevel("ERROR")
        path = table_path("gold", STREAM_TABLE)
        if args.fresh and DeltaTable.isDeltaTable(spark, path):
            # 원문 토픽을 처음부터 다시 읽으니 표도 비운다 — 예전 처리가 남긴 봉(이번에 안 만든
            # 분)이 섞이지 않게. 지운 것은 Delta 이력에 남는다(시간여행으로 볼 수 있음).
            DeltaTable.forPath(spark, path).delete()
        # 상태(분마다 집계 중인 값 · 중복 확인용 번호)를 RocksDB 에 — 메모리(JVM 힙) 대신 디스크로.
        spark.conf.set(
            "spark.sql.streaming.stateStore.providerClass",
            "org.apache.spark.sql.execution.streaming.state.RocksDBStateStoreProvider",
        )
        spark.conf.set("spark.sql.streaming.numRecentProgressUpdates", "100000")
        raw = (
            spark.readStream.format("kafka")
            .option("kafka.bootstrap.servers", args.bootstrap)
            .option("subscribe", args.topic)
            .option("startingOffsets", args.starting_offsets)
            .option("maxOffsetsPerTrigger", args.max_offsets)
            .load()
        )
        run_id = str(handle.run_id)
        written = {"bars": 0}

        def sink(batch: Any, batch_id: int) -> None:
            written["bars"] += upsert_bars(batch, batch_id, run_id)

        writer = (
            stream_bars(parse_raw(raw), args.watermark)
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


def _progress_summary(progress: dict[str, Any]) -> dict[str, object]:
    """[쓰임] 묶음 진행 정보 중 사람이 볼 것만 — 묶음 번호 · 받은 줄 · 초당 처리 · 워터마크."""
    return {
        "batch": progress.get("batchId"),
        "input_rows": progress.get("numInputRows"),
        "rows_per_s": round(float(progress.get("processedRowsPerSecond") or 0)),
        "watermark": progress.get("eventTime", {}).get("watermark"),
        "dropped_by_watermark": _dropped_by_watermark(progress),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    logging.getLogger("py4j").setLevel(logging.WARNING)  # foreachBatch 콜백마다 찍는 줄 숨김
    parser = argparse.ArgumentParser(prog="python -m mdp.stream.bars_1m")
    parser.add_argument(
        "--trigger", choices=["available-now", "continuous"], default="available-now"
    )
    parser.add_argument("--processing-time", default="10 seconds", help="계속 돌 때 묶음 간격")
    parser.add_argument("--duration-s", type=float, default=600.0, help="계속 돌 때 멈출 때까지")
    parser.add_argument("--watermark", default="1 minute", help="늦게 온 체결을 기다리는 시간")
    parser.add_argument("--max-offsets", type=int, default=200_000, help="묶음 하나의 최대 메시지")
    parser.add_argument("--topic", default=RAW_TOPIC)
    parser.add_argument("--starting-offsets", default="earliest", help="체크포인트가 없을 때만")
    parser.add_argument("--checkpoint-name", default="default")
    parser.add_argument(
        "--fresh", action="store_true", help="체크포인트 · 표 내용을 지우고 원문 처음부터"
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
