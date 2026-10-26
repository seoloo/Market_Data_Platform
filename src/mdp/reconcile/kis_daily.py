"""국내 주식 매일 대사 — 스트림 1분봉 ↔ 분봉 조회(배치)를 분마다 맞대고 원인 이름을 붙인다(K-4).

언제 쓰나: 장이 끝나고 그 날 분봉을 받아 Silver 를 다시 만든 뒤(K-2 · K-3), Spark 이미지 안에서::

    docker compose --profile spark run --rm spark \\
        python -m mdp.reconcile.kis_daily --date 2026-10-27

읽고 쓰는 것:
- 읽기: ``<lake>/gold/kr_stream_bars_1m`` (스트림),
  ``<lake>/silver/kr_minute_bars`` (배치, 정규장 · 마감 봉)
- 쓰기 (``<lake>/gold/``, Delta):
  ``kr_reconcile_minutes`` (그 날 칸만 바꿔 끼움 — 종목 · 분마다 결과),
  ``kr_reconcile_daily`` (더하기만 — 돌릴 때마다 종목 · 날 한 줄),
  실행 기록(작업 ``reconcile.kis_daily``)

분마다 결과 (``status``):

| 결과 | 뜻 |
|---|---|
| ``matched`` | 시가 · 고가 · 저가 · 종가 · 거래량이 모두 같다 |
| ``stream_missing`` | 배치에만 있다 — 그 분에 체결을 하나도 못 받았다(끊김 · 시작 전 · 끝난 뒤) |
| ``only_stream`` | 스트림에만 있다 |
| ``missed_trades`` | 값이 다르고, 스트림 누적 거래량이 분 안 · 앞 분과 이어지지 않는다(놓침) |
| ``values_differ`` | 값이 다른데 누적 거래량은 이어진다 — 분 경계 · 가격 기준 등(확인 필요) |

[이유] 원천에 체결 이력이 없어 배치 '정답' 은 분봉 조회다(ADR 0012). 원인은 누적 거래량으로 가른다 —
       체결 번호가 없어도 '앞 누적 + 이번 체결량 = 이번 누적' 이 깨지면 그 사이 체결을 놓친 것이다.
       장 후(16:00 ~) 봉은 실시간 체결 TR 이 다르므로(시간외 체결) 맞대지 않는다.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from typing import Any, Final

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.spark.session import spark_session, table_path

__all__ = ["STATUSES", "classify_minutes", "main", "run_reconcile", "summarize"]

STATUSES: Final = ("matched", "stream_missing", "only_stream", "missed_trades", "values_differ")
VALUES: Final = ("open", "high", "low", "close", "volume")


def classify_minutes(stream: Any, batch: Any, run_id: str) -> Any:
    """[쓰임] 그 날 스트림 봉 · 배치 봉(같은 날, 정규장 · 마감 봉) → 종목 · 분마다 결과 한 줄.
    [이유] 앞 분과의 이음(``boundary_contiguous``)도 본다 — 분 사이에 놓친 체결은 그 분 안의
           이음만으로는 보이지 않는다."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    ordered = Window.partitionBy("symbol", "trade_date").orderBy("bar_time")
    before_first = F.col("first_cumulative") - F.col("first_trade_volume")
    s = (
        stream.withColumn("prev_last_cumulative", F.lag("last_cumulative").over(ordered))
        .withColumn(
            "boundary_contiguous",
            F.col("prev_last_cumulative").isNull()
            | (F.col("prev_last_cumulative") == before_first),
        )
        .select(
            "symbol",
            "trade_date",
            "bar_time",
            *(F.col(c).alias(f"s_{c}") for c in VALUES),
            "acml_contiguous",
            "boundary_contiguous",
            "trade_count",
            "last_received_ms",
        )
    )
    b = batch.where(F.col("session").isin("regular", "closing_auction")).select(
        "symbol", "trade_date", "bar_time", "session", *(F.col(c).alias(f"b_{c}") for c in VALUES)
    )
    joined = s.join(b, ["symbol", "trade_date", "bar_time"], "full_outer")
    same = F.lit(True)
    for c in VALUES:
        same = same & (F.col(f"s_{c}") == F.col(f"b_{c}"))
    status = (
        F.when(F.col("s_close").isNull(), "stream_missing")
        .when(F.col("b_close").isNull(), "only_stream")
        .when(same, "matched")
        .when(~F.col("acml_contiguous") | ~F.col("boundary_contiguous"), "missed_trades")
        .otherwise("values_differ")
    )
    return (
        joined.withColumn("status", status)
        .withColumn("volume_gap", F.col("b_volume") - F.col("s_volume"))
        .withColumn("run_id", F.lit(run_id))
    )


def summarize(minutes: Any, run_id: str) -> Any:
    """[쓰임] 종목 · 날마다 한 줄 — 결과별 분 수 · 스트림 첫 · 마지막 분 · 거래량 합(양쪽)."""
    from pyspark.sql import functions as F

    counts = [
        F.sum(F.when(F.col("status") == name, 1).otherwise(0)).alias(name) for name in STATUSES
    ]
    return (
        minutes.groupBy("symbol", "trade_date")
        .agg(
            F.count(F.lit(1)).alias("minutes"),
            *counts,
            F.min(F.when(F.col("s_close").isNotNull(), F.col("bar_time"))).alias("stream_first"),
            F.max(F.when(F.col("s_close").isNotNull(), F.col("bar_time"))).alias("stream_last"),
            F.sum("s_volume").alias("stream_volume"),
            F.sum("b_volume").alias("batch_volume"),
        )
        .withColumn("reconciled_at", F.current_timestamp())
        .withColumn("run_id", F.lit(run_id))
    )


def run_reconcile(day: date, recorder: RunRecorder) -> dict[str, tuple[int, int]]:
    """[쓰임] 그 날 대사 — 분 결과는 그 날 칸만 바꿔 끼우고, 요약은 더하기만 한다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F

    written: dict[str, tuple[int, int]] = {}
    with record_run(recorder, "reconcile.kis_daily", {"date": day.isoformat()}) as run:
        spark = spark_session("reconcile.kis_daily")
        spark.sparkContext.setLogLevel("ERROR")
        on_day = F.col("trade_date") == F.lit(day)
        stream = (
            spark.read.format("delta").load(table_path("gold", "kr_stream_bars_1m")).where(on_day)
        )
        batch = (
            spark.read.format("delta").load(table_path("silver", "kr_minute_bars")).where(on_day)
        )
        minutes = classify_minutes(stream, batch, str(run.run_id)).cache()
        if minutes.limit(1).count() == 0:
            raise FileNotFoundError(f"{day} 의 스트림 봉도 배치 봉도 없다")
        path = table_path("gold", "kr_reconcile_minutes")
        writer = minutes.write.format("delta").partitionBy("trade_date")
        if DeltaTable.isDeltaTable(spark, path):
            writer.mode("overwrite").option("replaceWhere", f"trade_date = '{day}'").save(path)
        else:
            writer.mode("overwrite").save(path)
        summary = summarize(minutes, str(run.run_id))
        summary_path = table_path("gold", "kr_reconcile_daily")
        summary.write.format("delta").mode("append").save(summary_path)
        for name, table, rows in (
            ("kr_reconcile_minutes", path, minutes.count()),
            ("kr_reconcile_daily", summary_path, summary.count()),
        ):
            version = int(DeltaTable.forPath(spark, table).history(1).collect()[0]["version"])
            written[name] = (version, int(rows))
            run.add(
                Artifact(
                    f"gold/{name}",
                    "gold",
                    day.isoformat(),
                    row_count=int(rows),
                    table_version=version,
                )
            )
        for row in summary.collect():
            log_json(event="reconcile", **{k: str(v) for k, v in row.asDict().items()})
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.reconcile.kis_daily")
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run_reconcile(args.date, recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        run_reconcile(args.date, NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
