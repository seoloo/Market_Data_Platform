"""Gold 1 분봉 → PostgreSQL 복사와 체크섬 대조.

언제 쓰나: Gold 1 분봉(:mod:`mdp.gold.bars_1m`)을 만든 뒤, 호스트에서::

    python -m mdp.serve.bars_1m --symbol BTCUSDT --month 2024-03

읽고 쓰는 것: ``<lake>/gold/bars_1m`` (DuckDB 로 읽기) → PostgreSQL ``gold_bars_1m`` (그 달만 지우고
              다시 넣음) · 실행 기록(작업 ``serve.bars_1m``, 체크섬 · 행 수 · 복사한 Delta 버전).

흐름:
1. PostgreSQL 마이그레이션 적용(표가 없으면 만든다).
2. DuckDB 가 PostgreSQL 을 붙여(ATTACH) **한 트랜잭션** 안에서 그 달을 지우고 Delta 의 그 달을
   넣는다 — 중간에 실패하면 지우기까지 되돌아가 반쪽 복사가 남지 않는다.
3. 같은 행을 같은 순서 · 같은 글자 모양으로 이어 붙인 SHA-256 을 **Delta 쪽(DuckDB)** 과
   **PostgreSQL 쪽(자체 SQL)** 에서 따로 계산해 비교한다. 다르면 :class:`DataIntegrityError`.

[이유] 같은 데이터가 두 곳에 있으면 어긋날 수 있다 — 원본은 Delta 하나로 정하고, 복사본은 복사할
       때마다 체크섬으로 같은지 증명한다(README 설계 결정). 두 엔진이 소수를 글자로 바꾸는 모양이
       같아 (``61203.40000000``) 글자 해시로 비교할 수 있다.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.errors import DataIntegrityError
from mdp.query.duck import connect, delta_source
from mdp.runlog.recorder import Artifact, PostgresRecorder, record_run
from mdp.spark.session import table_path

__all__ = ["CopyResult", "copy_month", "latest_delta_version", "main"]

COLUMNS: Final = (
    "symbol",
    "bar_start_ms",
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
)
"""체크섬에 넣는 칸 (복사 정보 ``gold_version`` · ``loaded_run_id`` 는 빼고 데이터만)."""
_ROW_TEXT: Final = "concat_ws('|', " + ", ".join(COLUMNS) + ")"


@dataclass(frozen=True, slots=True)
class CopyResult:
    """[쓰임] 한 달 복사 결과."""

    rows: int
    gold_version: int
    delta_sha256: str
    postgres_sha256: str


def latest_delta_version(table_dir: Path) -> int:
    """[쓰임] Delta 표의 최신 버전 번호 — ``_delta_log/<번호>.json`` 중 가장 큰 번호.
    [이유] 복사본이 원본의 몇 번째 버전에서 왔는지 줄마다 남기려고(계보)."""
    versions = [
        int(p.stem)
        for p in (table_dir / "_delta_log").glob("*.json")
        if re.fullmatch(r"\d+", p.stem)
    ]
    if not versions:
        raise FileNotFoundError(f"Delta 표가 아니다: {table_dir}")
    return max(versions)


def _month_ms(month: date) -> tuple[int, int]:
    """[쓰임] 그 달 [첫 ms, 다음 달 첫 ms) — UTC."""
    from datetime import UTC, datetime

    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    end = datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def copy_month(
    symbol: str, month: date, pg_dsn: str, run_id: str, schema: str = "public"
) -> CopyResult:
    """[쓰임] 흐름 1 ~ 3 — 그 종목 · 그 달 Gold 1 분봉을 PostgreSQL 로 옮기고 체크섬을 맞춘다.
    [이유] ``schema`` 는 시험이 실제 표를 건드리지 않게 따로 만든 스키마에 쓰려고 둔다(DSN 의
           ``search_path`` 와 같게 준다)."""
    import psycopg

    from mdp.runlog.migrate import apply_migrations

    # 1 단계: 표 준비.
    with psycopg.connect(pg_dsn, autocommit=True) as pg:
        apply_migrations(pg)
    start_ms, end_ms = _month_ms(month)
    gold_dir = Path(table_path("gold", "bars_1m"))
    version = latest_delta_version(gold_dir)
    source = delta_source("gold", "bars_1m", version)
    where = f"symbol = '{symbol}' AND bar_start_ms >= {start_ms} AND bar_start_ms < {end_ms}"

    # 2 단계: 한 트랜잭션 안에서 그 달 지우고 넣기.
    con = connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    attach_dsn = pg_dsn.replace(
        "'", "''"
    )  # 접속 문자열 안 따옴표(options='-c …')가 SQL 을 깨지 않게
    con.execute(f"ATTACH '{attach_dsn}' AS pg (TYPE postgres)")
    columns = ", ".join(COLUMNS)
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(f"DELETE FROM pg.{schema}.gold_bars_1m WHERE {where}")
        con.execute(
            f"""INSERT INTO pg.{schema}.gold_bars_1m
                ({columns}, bar_start, gold_version, loaded_run_id)
                SELECT {columns}, bar_start, {version}, '{run_id}'::UUID
                FROM {source} WHERE {where}"""
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    # 3 단계: 양쪽 체크섬.
    delta_row = con.execute(
        f"SELECT count(*), sha256(coalesce(string_agg({_ROW_TEXT}, '~' ORDER BY bar_start_ms), ''))"
        f" FROM {source} WHERE {where}"
    ).fetchone()
    con.close()
    with psycopg.connect(pg_dsn) as pg:
        pg_row = pg.execute(
            f"SELECT count(*), encode(sha256(convert_to("
            f"coalesce(string_agg({_ROW_TEXT}, '~' ORDER BY bar_start_ms), ''), 'UTF8')), 'hex')"
            f" FROM {schema}.gold_bars_1m WHERE {where}"
        ).fetchone()
    assert delta_row is not None and pg_row is not None
    result = CopyResult(int(pg_row[0]), version, str(delta_row[1]), str(pg_row[1]))
    if int(delta_row[0]) != result.rows or result.delta_sha256 != result.postgres_sha256:
        raise DataIntegrityError(
            f"복사본이 원본과 다르다: 행 {delta_row[0]} vs {pg_row[0]}, "
            f"체크섬 {result.delta_sha256[:16]} vs {result.postgres_sha256[:16]}"
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 — 복사하고 결과를 남긴다. PostgreSQL 주소는 꼭 있어야 한다(복사할 곳)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.serve.bars_1m")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if not args.pg_dsn:
        parser.error("복사할 PostgreSQL 주소가 없다 (--pg-dsn 또는 MDP_PG_DSN)")
    year_text, month_text = args.month.split("-")
    month = date(int(year_text), int(month_text), 1)
    with (
        PostgresRecorder(args.pg_dsn) as recorder,
        record_run(recorder, "serve.bars_1m", {"symbol": args.symbol, "month": args.month}) as run,
    ):
        result = copy_month(args.symbol, month, args.pg_dsn, str(run.run_id))
        run.add(
            Artifact(
                "postgres/gold_bars_1m",
                "gold",
                f"copied_from_v{result.gold_version}",
                sha256=result.postgres_sha256,
                row_count=result.rows,
                table_version=result.gold_version,
            )
        )
        log_json(
            event="copied",
            rows=result.rows,
            gold_version=result.gold_version,
            checksum_equal=result.delta_sha256 == result.postgres_sha256,
            sha256=result.postgres_sha256,
            run_id=str(run.run_id),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
