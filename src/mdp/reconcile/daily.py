"""매일 대사 — 스트림 봉 = 배치 봉? 다르면 원인을 나누고, 원하면 배치 봉으로 메운다.

언제 쓰나: 그날의 배치 Gold(일 파일 → Silver → Gold)와 스트림 봉이 다 있을 때, Spark 이미지 안에서::

    docker compose run --rm spark python -m mdp.reconcile.daily --symbol BTCUSDT --date 2024-03-01
    docker compose run --rm spark python -m mdp.reconcile.daily --symbol BTCUSDT \\
        --date 2024-03-01 --repair

읽고 쓰는 것:
- 읽기: Delta ``gold/bars_1m`` (배치 봉) · ``gold/stream_bars_1m`` (스트림 봉) ·
  ``silver/agg_trades`` (그날 체결 번호) · Kafka 원문 토픽(받은 체결 번호 · 받은 시각 · 출처)
- 쓰기: PostgreSQL ``reconcile_daily`` · ``reconcile_minutes`` (더하기만) · 실행 기록(작업
  ``reconcile.daily``) · ``--repair`` 면 ``gold/stream_bars_1m`` 의 다른 분을 배치 봉으로 MERGE

분마다 상태 (배치 봉이 있는 분은 정확히 하나):

| 상태 | 뜻 |
|---|---|
| ``matched`` | 스트림이 만든 봉이 배치와 :data:`~mdp.gold.bars_1m.BAR_COLUMNS` 모두 같음 |
| ``repaired`` | 같지만 전에 대사가 배치 봉으로 바꿔 넣은 분 (스트림 몫으로 세지 않음) |
| ``stream_gap`` | 그 분 체결 중 **원문 토픽에 없는 것**이 있음 — 끊김 · PC 잠듦 · 받기 시작 전 |
| ``late`` | 빠진 체결이 모두 원문 토픽에는 있음 — 받았지만 워터마크 뒤라 버림 |
| ``duplicate`` | 스트림이 체결을 배치보다 더 셈 — 중복 제거 실패 |
| ``other`` | 체결 수는 같은데 값이 다름 — 원인 미상, 사람이 확인 |

배치에 없는 분이 스트림에만 있으면 ``only_stream`` (하루 분 수에는 넣지 않음).

[이유] '다르다' 만으로는 고칠 수 없다 — 원인에 따라 할 일이 다르다(끊김이면 감시 · 재연결, 늦음이면
       워터마크, 중복이면 코드). 받지 못한 것과 받았지만 버린 것은 **원문 토픽**을 보면 가를 수
       있다: 빠진 체결 번호가 토픽에 있으면 늦음, 없으면 끊김. 메우기는 배치 봉을 그대로 넣고 표시만
       해 두며(``repaired``), 바꾸기 전 스트림 봉은 Delta 이력에 남는다.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Final

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.gold.bars_1m import BAR_COLUMNS, MINUTE_MS
from mdp.runlog.recorder import Artifact, PostgresRecorder, RunRecorder, record_run
from mdp.spark.session import spark_session, table_path
from mdp.stream.bars_1m import STREAM_TABLE, ensure_repair_columns, parse_raw
from mdp.stream.messages import RAW_TOPIC

__all__ = ["CAUSES", "classify_minutes", "main", "repair_minutes", "summarize"]

CAUSES: Final = ("stream_gap", "late", "duplicate", "other")
"""배치 봉이 있는데 다른 분의 원인 (상태 표 참고)."""


def classify_minutes(batch_bars: Any, stream_bars: Any, batch_trades: Any, received: Any) -> Any:
    """[쓰임] 분마다 상태 · 원인 (위 표).
              입력: 배치 봉 · 스트림 봉(``repaired`` 칸 포함) · 그날 배치 체결(종목 · 묶음 번호 ·
              체결 시각) · 받은 체결(종목 · 묶음 번호, 원문 토픽에서).
              나옴: 종목 · ``bar_start_ms`` · ``status`` · 두 봉의 묶음 수 · ``not_received`` ·
              ``late_trades`` · ``differing_columns``.
    [이유] 빠진 체결 수 = 배치 묶음 수 − 스트림 묶음 수 이고, 그중 원문 토픽에 없는 것이
           ``not_received`` 다. 나머지(받았는데 빠진 것)가 ``late_trades`` — 번호를 하나하나 봉에
           남기지 않아도 정확히 나눌 수 있다."""
    from pyspark.sql import functions as F

    minute = F.col("transact_time_ms") - F.col("transact_time_ms") % MINUTE_MS
    not_received = (
        batch_trades.join(received, ["symbol", "agg_trade_id"], "left_anti")
        .groupBy("symbol", minute.alias("bar_start_ms"))
        .agg(F.count("*").alias("not_received"))
    )
    b = batch_bars.select("symbol", "bar_start_ms", *BAR_COLUMNS).alias("b")
    s = stream_bars.select("symbol", "bar_start_ms", *BAR_COLUMNS, "repaired").alias("s")
    joined = b.join(
        s,
        (F.col("b.symbol") == F.col("s.symbol"))
        & (F.col("b.bar_start_ms") == F.col("s.bar_start_ms")),
        "full_outer",
    ).select(
        F.coalesce("b.symbol", "s.symbol").alias("symbol"),
        F.coalesce("b.bar_start_ms", "s.bar_start_ms").alias("bar_start_ms"),
        F.col("b.bar_start_ms").isNotNull().alias("in_batch"),
        F.col("s.bar_start_ms").isNotNull().alias("in_stream"),
        F.coalesce("s.repaired", F.lit(False)).alias("repaired"),
        F.col("b.agg_trade_count").alias("batch_agg_trades"),
        F.col("s.agg_trade_count").alias("stream_agg_trades"),
        F.array_compact(
            F.array(
                *[
                    F.when(F.col(f"b.{c}").eqNullSafe(F.col(f"s.{c}")), F.lit(None)).otherwise(
                        F.lit(c)
                    )
                    for c in BAR_COLUMNS
                ]
            )
        ).alias("differing_columns"),
    )
    with_counts = joined.join(not_received, ["symbol", "bar_start_ms"], "left").withColumn(
        "not_received", F.coalesce("not_received", F.lit(0)).cast("long")
    )
    both = F.col("in_batch") & F.col("in_stream")
    missing = F.col("batch_agg_trades") - F.coalesce("stream_agg_trades", F.lit(0))
    status = (
        F.when(~F.col("in_batch"), "only_stream")
        .when(both & (F.size("differing_columns") == 0) & F.col("repaired"), "repaired")
        .when(both & (F.size("differing_columns") == 0), "matched")
        .when(both & (F.col("stream_agg_trades") > F.col("batch_agg_trades")), "duplicate")
        .when((missing > 0) & (F.col("not_received") > 0), "stream_gap")
        .when(missing > 0, "late")
        .otherwise("other")
    )
    gap_or_late = F.col("status").isin("stream_gap", "late")
    late_trades = F.greatest(missing - F.col("not_received"), F.lit(0))
    return (
        with_counts.withColumn("status", status)
        .withColumn(
            "late_trades", F.when(gap_or_late, late_trades).otherwise(F.lit(0)).cast("long")
        )
        .withColumn(
            "differing_columns",
            F.when(both, F.col("differing_columns")).otherwise(F.array().cast("array<string>")),
        )
        .drop("in_batch", "in_stream", "repaired")
    )


def summarize(minutes: Sequence[dict[str, Any]]) -> dict[str, int]:
    """[쓰임] 분마다 상태 → 하루 집계 (``reconcile_daily`` 의 숫자 칸)."""
    counts = dict.fromkeys(("matched", "repaired", *CAUSES, "only_stream"), 0)
    trades_not_received = trades_late = 0
    for row in minutes:
        counts[row["status"]] += 1
        if row["status"] in CAUSES:  # 메운 분은 이미 배치 봉이라 빼고 센다
            trades_not_received += int(row["not_received"])
            trades_late += int(row["late_trades"])
    batch_minutes = sum(counts[k] for k in ("matched", "repaired", *CAUSES))
    stream_minutes = sum(1 for row in minutes if row["stream_agg_trades"] is not None)
    return {
        "batch_minutes": batch_minutes,
        "stream_minutes": stream_minutes,
        **counts,
        "trades_not_received": trades_not_received,
        "trades_late": trades_late,
    }


def repair_minutes(spark: Any, batch_bars: Any, bar_starts: Sequence[int], run_id: str) -> int:
    """[쓰임] 고른 분의 스트림 봉을 배치 봉으로 바꿔 넣는다(없으면 넣는다). ``repaired = true``.
    [이유] 배치 봉은 바이낸스 일 파일에서 만든 '확정된 답' 이다. 스트림이 놓친 분을 그것으로 메워
           스트림 표 하나만 봐도 빈 곳이 없게 한다. 바꾸기 전 봉은 Delta 이력에 남는다."""
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F

    if not bar_starts:
        return 0
    path = table_path("gold", STREAM_TABLE)
    ensure_repair_columns(spark, path)
    rows = (
        batch_bars.where(F.col("bar_start_ms").isin(list(bar_starts)))
        .select("symbol", "bar_start_ms", *BAR_COLUMNS)
        .withColumn("last_received_ms", F.lit(None).cast("long"))
        .withColumn(
            "ids_contiguous",
            F.col("last_agg_trade_id") - F.col("first_agg_trade_id") + 1
            == F.col("agg_trade_count"),
        )
        .withColumn("bar_start", F.timestamp_millis("bar_start_ms"))
        .withColumn("trade_date", F.to_date("bar_start"))
        .withColumn("batch_id", F.lit(None).cast("long"))
        .withColumn("run_id", F.lit(run_id))
        .withColumn("repaired", F.lit(True))
        .withColumn("repair_run_id", F.lit(run_id))
    )
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
    return len(bar_starts)


def _latest_version(spark: Any, layer: str, name: str) -> int:
    """[쓰임] Delta 표의 최신 버전."""
    from delta.tables import DeltaTable

    return int(
        DeltaTable.forPath(spark, table_path(layer, name)).history(1).collect()[0]["version"]
    )


def _received(
    spark: Any, bootstrap: str, topic: str, symbol: str, day: date
) -> tuple[Any, dict[str, Any]]:
    """[쓰임] 원문 토픽 전체를 한 번 읽어 그날 받은 체결 번호와, 출처 · 받은 시각 지연 통계를 낸다.
    [이유] 토픽은 보관 7 일이라 대사는 그 안에 돌아야 한다. 받은 시각 − 체결 시각은 실시간일 때만
           뜻이 있다(재생은 받은 시각 = 재생한 시각)."""
    from pyspark.sql import functions as F

    raw = (
        spark.read.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", topic)
        .option("startingOffsets", "earliest")
        .option("endingOffsets", "latest")
        .option("includeHeaders", "true")
        .load()
        .withColumn(
            "source", F.expr("decode(filter(headers, h -> h.key = 'source')[0].value, 'UTF-8')")
        )
    )
    trades = (
        parse_raw(raw, keep=["source"])
        .where((F.col("symbol") == symbol) & (F.to_date("event_time") == F.lit(day)))
        .cache()
    )
    sources = sorted(r["source"] or "unknown" for r in trades.select("source").distinct().collect())
    stats: dict[str, Any] = {"stream_source": "+".join(sources) if sources else None}
    lag = trades.where(F.col("source") == "live").select(
        (F.col("received_ms") - F.col("transact_time_ms")).alias("lag")
    )
    row = lag.agg(
        F.percentile_approx("lag", 0.5).alias("p50"),
        F.percentile_approx("lag", 0.95).alias("p95"),
        F.max("lag").alias("max"),
    ).collect()[0]
    stats |= {
        "ingest_lag_p50_ms": row["p50"],
        "ingest_lag_p95_ms": row["p95"],
        "ingest_lag_max_ms": row["max"],
    }
    return trades.select("symbol", "agg_trade_id").distinct(), stats


def _write_pg(dsn: str, daily: dict[str, Any], mismatches: Sequence[dict[str, Any]]) -> None:
    """[쓰임] 하루 한 줄 + 다른 분들을 한 트랜잭션으로 더한다."""
    import psycopg

    columns = list(daily)
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute(
            f"INSERT INTO reconcile_daily ({', '.join(columns)})"
            f" VALUES ({', '.join(['%s'] * len(columns))})",
            [daily[c] for c in columns],
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO reconcile_minutes (run_id, symbol, bar_start_ms, cause,"
                " batch_agg_trades, stream_agg_trades, not_received, late_trades,"
                " differing_columns)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        daily["run_id"],
                        m["symbol"],
                        m["bar_start_ms"],
                        m["status"],
                        m["batch_agg_trades"],
                        m["stream_agg_trades"],
                        m["not_received"],
                        m["late_trades"],
                        list(m["differing_columns"]),
                    )
                    for m in mismatches
                ],
            )


def run_reconcile(args: argparse.Namespace, recorder: RunRecorder) -> dict[str, Any]:
    """[쓰임] 하루 대사 — 읽기 → 분마다 상태 → PostgreSQL → (``--repair``) 메우기."""
    from pyspark.sql import functions as F

    params = {
        "symbol": args.symbol,
        "date": f"{args.date:%Y-%m-%d}",
        "repair": args.repair,
        "topic": args.topic,
    }
    with record_run(recorder, "reconcile.daily", params) as handle:
        run_id = str(handle.run_id)
        spark = spark_session("reconcile.daily")
        spark.sparkContext.setLogLevel("ERROR")
        on_day = (F.col("symbol") == args.symbol) & (F.col("trade_date") == F.lit(args.date))
        batch_version = _latest_version(spark, "gold", "bars_1m")
        stream_version = _latest_version(spark, "gold", STREAM_TABLE)
        batch_bars = (
            spark.read.format("delta").load(table_path("gold", "bars_1m")).where(on_day).cache()
        )
        stream_path = table_path("gold", STREAM_TABLE)
        ensure_repair_columns(spark, stream_path)
        stream_bars = (
            spark.read.format("delta")
            .load(stream_path)
            .where(on_day)
            .withColumn("repaired", F.coalesce("repaired", F.lit(False)))
        )
        if batch_bars.limit(1).count() == 0:
            raise FileNotFoundError(
                f"배치 봉이 없다: {args.symbol} {args.date} — 일 파일 → Silver → Gold 먼저"
            )
        batch_trades = (
            spark.read.format("delta")
            .load(table_path("silver", "agg_trades"))
            .where(on_day)
            .select("symbol", "agg_trade_id", "transact_time_ms")
        )
        received, stats = _received(spark, args.bootstrap, args.topic, args.symbol, args.date)
        minutes = [
            r.asDict()
            for r in classify_minutes(batch_bars, stream_bars, batch_trades, received).collect()
        ]
        summary = summarize(minutes)
        mismatches = sorted(
            (m for m in minutes if m["status"] in (*CAUSES, "only_stream")),
            key=lambda m: m["bar_start_ms"],
        )
        to_repair = sorted(m["bar_start_ms"] for m in mismatches if m["status"] in CAUSES)
        repaired_now = repair_minutes(spark, batch_bars, to_repair, run_id) if args.repair else 0
        daily = {
            "run_id": run_id,
            "symbol": args.symbol,
            "trade_date": args.date,
            "reconciled_at": datetime.now(UTC),
            **summary,
            "match_rate": round(summary["matched"] / summary["batch_minutes"], 6),
            "repaired_now": repaired_now,
            **stats,
            "batch_version": batch_version,
            "stream_version": stream_version,
        }
        if args.pg_dsn:
            _write_pg(args.pg_dsn, daily, mismatches)
        handle.add(
            Artifact(
                f"gold/{STREAM_TABLE}",
                "stream",
                "repaired" if repaired_now else "reconciled",
                row_count=repaired_now,
                table_version=_latest_version(spark, "gold", STREAM_TABLE)
                if repaired_now
                else stream_version,
            )
        )
        printable = {
            k: (str(v) if isinstance(v, (date, datetime)) else v) for k, v in daily.items()
        }
        log_json(event="reconcile", **printable)
        for m in mismatches[: args.show]:
            log_json(
                event="mismatch",
                minute=datetime.fromtimestamp(m["bar_start_ms"] / 1000, UTC).strftime("%H:%M"),
                cause=m["status"],
                batch=m["batch_agg_trades"],
                stream=m["stream_agg_trades"],
                not_received=m["not_received"],
                late=m["late_trades"],
            )
    return daily


def _parse_date(text: str) -> date:
    """[쓰임] ``YYYY-MM-DD`` → 날짜."""
    return date.fromisoformat(text)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서). 모두 같거나 메웠으면 0, 원인이 남았으면 1."""
    setup_cli_logging()
    logging.getLogger("py4j").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m mdp.reconcile.daily")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date", required=True, type=_parse_date, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--repair", action="store_true", help="다른 분을 배치 봉으로 메운다")
    parser.add_argument("--show", type=int, default=20, help="로그에 보일 다른 분 수")
    parser.add_argument("--topic", default=RAW_TOPIC)
    parser.add_argument("--bootstrap", default=os.environ.get("MDP_KAFKA_BOOTSTRAP", "kafka:9092"))
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if not args.pg_dsn:
        parser.error("대사 결과를 남길 PostgreSQL 이 필요하다 (MDP_PG_DSN)")
    with PostgresRecorder(args.pg_dsn) as recorder:
        daily = run_reconcile(args, recorder)
    remaining = sum(daily[c] for c in CAUSES)
    return 0 if remaining == 0 or daily["repaired_now"] == remaining else 1


if __name__ == "__main__":
    raise SystemExit(main())
