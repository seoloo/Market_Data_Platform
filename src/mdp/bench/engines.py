"""벤치마크 엔진 세 가지 — 같은 과제(:mod:`mdp.bench.result`)를 각자의 방식으로 푼다.

언제 쓰나: :mod:`mdp.bench.run` 이 엔진 하나를 골라 부른다(엔진마다 새 프로세스).
읽고 쓰는 것: Bronze 월 zip(연구 스크립트) 또는 압축 푼 CSV(DuckDB · Spark), 읽기만.

| 엔진 | 읽는 것 | 방식 |
|---|---|---|
| ``legacy-fast`` | zip | 연구 216 빠른 추출 — 32 MB 덩어리, 번호가 맞으면 첫 · 끝 줄만 |
| ``legacy-full`` | zip | 연구 216 전수 추출 — 한 줄씩 전부 (검산용 정답지) |
| ``duckdb`` | CSV | SQL 한 번 훑기 + 번호 순 LAG 창 함수 (8 스레드) |
| ``spark`` | CSV | DataFrame 집계 + 번호 칸별 LAG (Docker, ``local[8]``) |

CSV 를 글자로 읽고 ``try_cast`` 하는 것은 DuckDB · Spark 가 같다 — Silver 와 같은 읽기 방식이다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mdp.bench.result import HOUR_MS, MINUTE_MS, WINDOW_MS, WINDOW_STEP_MS

__all__ = ["run_duckdb", "run_legacy", "run_spark"]

_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)
_MICROSECOND_THRESHOLD = 10**14


def run_legacy(zip_path: Path, *, full: bool, repo_root: Path) -> dict[str, Any]:
    """[쓰임] 연구 216 추출기(``scripts/run_qflow_216.py``)를 그대로 불러 결과를 공통 모양으로
              바꾼다.
    [이유] 연구 코드는 고치지 않는다(절대 규칙 6). 파일 줄 순서로 세므로 중복 줄 수는 낼 수 없어
           None."""
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.run_qflow_216 import extract, extract_full

    result = (extract_full if full else extract)(zip_path)
    return {
        "rows": result.rows,
        "first_id": result.first_id,
        "last_id": result.last_id,
        "first_ms": result.first_ts,
        "last_ms": result.last_ts,
        "gaps": sorted([list(gap) for gap in result.gaps if gap[2] > 0]),
        "duplicate_rows": None,
        "time_reversed": result.nonmono,
        "check_minutes": {str(k): list(v) for k, v in result.check_min.items()},
        "windows": {str(k): list(v) for k, v in result.windows.items()},
    }


_DUCKDB_TYPED = f"""
    WITH raw AS (
        SELECT * FROM read_csv(?, header = false, all_varchar = true,
                               names = {list(_COLUMNS)})
    ),
    typed AS (
        SELECT TRY_CAST(agg_trade_id AS BIGINT)        AS id,
               TRY_CAST(quantity AS DECIMAL(18, 8))    AS qty,
               CASE WHEN TRY_CAST(transact_time AS BIGINT) > {_MICROSECOND_THRESHOLD}
                    THEN TRY_CAST(transact_time AS BIGINT) // 1000
                    ELSE TRY_CAST(transact_time AS BIGINT) END AS ms,
               lower(is_buyer_maker) = 'true'          AS maker
        FROM raw
    )
    SELECT * FROM typed WHERE id IS NOT NULL AND qty IS NOT NULL AND ms IS NOT NULL
"""


def run_duckdb(csv_path: Path, *, threads: int = 8, memory_limit: str = "8GB") -> dict[str, Any]:
    """[쓰임] DuckDB 로 CSV 를 한 번 읽어 임시 표로 두고, 요약 · 빈 곳 · 점검 분 · 창을 SQL 로 센다.
    [이유] 형식 맞추기를 임시 표로 한 번만 하고(Silver 최적화와 같은 생각), 나머지는 그 표에서
           센다."""
    import duckdb

    con = duckdb.connect()
    con.execute(f"SET threads = {threads}")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"CREATE TEMP TABLE trades AS {_DUCKDB_TYPED}", [str(csv_path)])
    summary = con.execute(
        "SELECT count(*), min(id), max(id), min(ms), max(ms),"
        " count(*) - count(DISTINCT id) FROM trades"
    ).fetchone()
    assert summary is not None
    # 번호 순 이웃 비교는 6,651 만 줄 정렬이라 한 번만 — 빈 곳 · 역행 후보만 작은 임시 표로 남긴다.
    con.execute(
        """CREATE TEMP TABLE pairs AS
           SELECT * FROM (
               SELECT id, ms, lag(id) OVER w AS prev_id, lag(ms) OVER w AS prev_ms
               FROM trades WINDOW w AS (ORDER BY id, ms)
           ) WHERE id - prev_id > 1 OR ms < prev_ms"""
    )
    gaps = con.execute(
        "SELECT prev_ms, ms, id - prev_id - 1 FROM pairs WHERE id - prev_id > 1 ORDER BY 1, 2"
    ).fetchall()
    reversed_rows = con.execute("SELECT count(*) FROM pairs WHERE ms < prev_ms").fetchone()
    assert reversed_rows is not None
    minutes = con.execute(
        f"""SELECT ms - ms % {MINUTE_MS} AS m, sum(qty), sum(qty) FILTER (WHERE NOT maker), count(*)
            FROM trades WHERE (ms - ms % {MINUTE_MS}) % {HOUR_MS} = 0 GROUP BY m"""
    ).fetchall()
    windows = con.execute(
        f"""SELECT ms - ms % {MINUTE_MS} AS w, sum(qty) FILTER (WHERE NOT maker),
                   sum(qty) FILTER (WHERE maker), count(*)
            FROM trades
            WHERE (ms - ms % {MINUTE_MS}) % {WINDOW_STEP_MS} = 0 AND ms % {MINUTE_MS} < {WINDOW_MS}
            GROUP BY w"""
    ).fetchall()
    return {
        "rows": summary[0],
        "first_id": summary[1],
        "last_id": summary[2],
        "first_ms": summary[3],
        "last_ms": summary[4],
        "gaps": [[int(a), int(b), int(c)] for a, b, c in gaps],
        "duplicate_rows": summary[5],
        "time_reversed": reversed_rows[0],
        "check_minutes": {str(m): [str(q), str(b or 0), n] for m, q, b, n in minutes},
        "windows": {str(w): [str(b or 0), str(s or 0), n] for w, b, s, n in windows},
    }


def run_spark(csv_path: str) -> dict[str, Any]:
    """[쓰임] Spark 로 같은 과제를 푼다 — 형식 맞춘 DataFrame 을 캐시해 두고 집계 · 칸별 LAG.
    [이유] 빈 곳 · 역행은 Silver 와 같은 방법(번호를 100 만 칸으로 나눠 칸 안 LAG + 칸 경계)을
           쓴다."""
    from pyspark import StorageLevel
    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812 — PySpark 관례
    from pyspark.sql.types import StringType, StructField, StructType

    from mdp.spark.session import spark_session

    spark = spark_session("bench")
    spark.sparkContext.setLogLevel("ERROR")
    schema = StructType([StructField(name, StringType()) for name in _COLUMNS])
    raw = spark.read.schema(schema).option("header", "false").csv(csv_path)
    raw_ms = F.expr("try_cast(transact_time AS bigint)")
    trades = (
        raw.select(
            F.expr("try_cast(agg_trade_id AS bigint)").alias("id"),
            F.expr("try_cast(quantity AS decimal(18,8))").alias("qty"),
            F.when(raw_ms > _MICROSECOND_THRESHOLD, F.expr("transact_time div 1000"))
            .otherwise(raw_ms)
            .alias("ms"),
            (F.lower("is_buyer_maker") == "true").alias("maker"),
        )
        .where("id IS NOT NULL AND qty IS NOT NULL AND ms IS NOT NULL")
        .persist(StorageLevel.MEMORY_AND_DISK)
    )
    summary = trades.agg(
        F.count("*"), F.min("id"), F.max("id"), F.min("ms"), F.max("ms"), F.countDistinct("id")
    ).collect()[0]

    keyed = trades.withColumn("bucket", F.floor(F.col("id") / 1_000_000))
    within = Window.partitionBy("bucket").orderBy("id", "ms")
    inner = keyed.select(
        F.lag("id").over(within).alias("prev_id"),
        F.col("id").alias("next_id"),
        F.lag("ms").over(within).alias("prev_ms"),
        F.col("ms").alias("next_ms"),
    ).where("prev_id IS NOT NULL")
    edges = keyed.groupBy("bucket").agg(
        F.min(F.struct("id", "ms")).alias("first"), F.max(F.struct("id", "ms")).alias("last")
    )
    across = Window.orderBy("bucket")
    boundary = edges.select(
        F.lag("last.id").over(across).alias("prev_id"),
        F.col("first.id").alias("next_id"),
        F.lag("last.ms").over(across).alias("prev_ms"),
        F.col("first.ms").alias("next_ms"),
    ).where("prev_id IS NOT NULL")
    pairs = inner.unionByName(boundary).persist(StorageLevel.MEMORY_AND_DISK)
    gaps = (
        pairs.where("next_id - prev_id > 1")
        .select("prev_ms", "next_ms", (F.col("next_id") - F.col("prev_id") - 1).alias("missing"))
        .collect()
    )
    reversed_rows = pairs.where("next_ms < prev_ms").count()

    minute = F.col("ms") - F.col("ms") % MINUTE_MS
    minutes = (
        trades.withColumn("m", minute)
        .where(F.col("m") % HOUR_MS == 0)
        .groupBy("m")
        .agg(F.sum("qty"), F.sum(F.when(~F.col("maker"), F.col("qty"))), F.count("*"))
        .collect()
    )
    windows = (
        trades.withColumn("w", minute)
        .where((F.col("w") % WINDOW_STEP_MS == 0) & (F.col("ms") % MINUTE_MS < WINDOW_MS))
        .groupBy("w")
        .agg(
            F.sum(F.when(~F.col("maker"), F.col("qty"))),
            F.sum(F.when(F.col("maker"), F.col("qty"))),
            F.count("*"),
        )
        .collect()
    )
    return {
        "rows": summary[0],
        "first_id": summary[1],
        "last_id": summary[2],
        "first_ms": summary[3],
        "last_ms": summary[4],
        "gaps": sorted([int(r[0]), int(r[1]), int(r[2])] for r in gaps),
        "duplicate_rows": summary[0] - summary[5],
        "time_reversed": reversed_rows,
        "check_minutes": {str(r[0]): [str(r[1]), str(r[2] or 0), r[3]] for r in minutes},
        "windows": {str(r[0]): [str(r[1] or 0), str(r[2] or 0), r[3]] for r in windows},
    }
