"""Silver 검산 — Spark 가 만든 표를 DuckDB SQL 로 다시 계산해 맞대 본다.

언제 쓰나: Silver 체결(:mod:`mdp.silver.agg_trades`)을 만든 뒤, 호스트에서::

    python -m mdp.query.check_silver --symbol BTCUSDT --month 2024-03

읽고 쓰는 것: ``silver_agg_trades`` · ``silver_agg_trades_audit_daily`` · ``silver_agg_trades_gaps``
              (Delta, 읽기만) → 결과 JSON 한 줄씩 · 실행 기록(작업 ``query.check_silver``).

검사 세 묶음:
1. **불변 조건** — Silver 깨끗한 체결이 늘 지켜야 하는 것: 번호 중복 없음 · 빈 칸 없음 ·
   날짜 = 체결 시각의 UTC 날짜 · 가격 · 수량 > 0 · 첫 번호 ≤ 끝 번호. 하나라도 어기면 실패.
2. **날 감사 재계산** — 날마다 행 수 · 최소/최대 번호 · 거래량 · 공격 매수량 · 날 안 빠진 번호를
   DuckDB 로 처음부터 다시 세어, Spark 가 쓴 ``audit_daily`` 와 **소수점까지** 같은지 본다.
   다르면 실패.
3. **빈 곳 · 역행 (참고)** — 깨끗한 체결을 번호 순으로 이웃 비교. Spark 의 빈 곳 표는 원본 파일
   단위(일 파일로 넘긴 줄 포함)라 뜻이 조금 달라, 합격 판정에는 쓰지 않고 함께 보여 준다.

[이유] 같은 계산을 **다른 엔진**(Spark ↔ DuckDB)으로 한 번 더 하면, 한쪽 엔진 · 한쪽 코드의 실수가
       드러난다. decimal 이라 두 엔진의 합계가 소수점까지 같아야 한다(float 였다면 더하는 순서
       때문에 못 했을 검사).
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.query.duck import connect, register_views
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run

if TYPE_CHECKING:
    import duckdb

__all__ = ["CheckResult", "main", "run_checks"]

TRADES: Final = "silver_agg_trades"
DAILY: Final = "silver_agg_trades_audit_daily"
GAPS: Final = "silver_agg_trades_gaps"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """[쓰임] 검사 하나의 결과. ``passed`` 가 None 이면 합격 판정 없는 참고 값."""

    name: str
    passed: bool | None
    detail: dict[str, object]


def _month_filter(symbol: str, month: date) -> str:
    """[쓰임] 그 종목 · 그 달 조건 (``trade_date`` 파티션만 읽게)."""
    next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return (
        f"symbol = '{symbol}' AND trade_date >= DATE '{month:%Y-%m-%d}'"
        f" AND trade_date < DATE '{next_month:%Y-%m-%d}'"
    )


def _invariants(con: duckdb.DuckDBPyConnection, where: str) -> list[CheckResult]:
    """[쓰임] 1 묶음 — 불변 조건을 한 번의 훑기로 센다(어긴 행 수가 모두 0 이어야 합격)."""
    row = con.execute(
        f"""
        SELECT
            count(*)                                               AS trade_rows,
            count(*) - count(DISTINCT agg_trade_id)                AS duplicate_ids,
            count(*) FILTER (WHERE agg_trade_id IS NULL OR price IS NULL OR quantity IS NULL
                                OR transact_time_ms IS NULL OR is_buyer_maker IS NULL) AS null_rows,
            count(*) FILTER (
                WHERE trade_date <> CAST(make_timestamp(transact_time_ms * 1000) AS DATE)
            )                                                      AS wrong_date,
            count(*) FILTER (WHERE price <= 0 OR quantity <= 0)    AS non_positive,
            count(*) FILTER (WHERE first_trade_id > last_trade_id) AS bad_trade_range
        FROM {TRADES} WHERE {where}
        """
    ).fetchone()
    assert row is not None
    names = [
        "trade_rows",
        "duplicate_ids",
        "null_rows",
        "wrong_date",
        "non_positive",
        "bad_trade_range",
    ]
    values = dict(zip(names, row, strict=True))
    results = [CheckResult("trade_rows", values["trade_rows"] > 0, {"rows": values["trade_rows"]})]
    for name in names[1:]:
        results.append(CheckResult(name, values[name] == 0, {"violations": values[name]}))
    return results


def _daily_recount(con: duckdb.DuckDBPyConnection, where: str) -> CheckResult:
    """[쓰임] 2 묶음 — 날 감사를 처음부터 다시 세어 Spark 의 ``audit_daily`` 와 칸마다 비교한다.
    [이유] 한쪽에만 있는 날도 '다름' 으로 잡으려고 FULL OUTER JOIN 을 쓴다."""
    columns = [
        "rows",
        "min_id",
        "max_id",
        "quantity_sum",
        "taker_buy_quantity_sum",
        "missing_ids_inside_day",
    ]
    differs = " OR ".join(f"d.{c} IS DISTINCT FROM s.{c}" for c in columns)
    mismatches = con.execute(
        f"""
        WITH recount AS (
            SELECT trade_date,
                   count(*)                                             AS rows,
                   min(agg_trade_id)                                    AS min_id,
                   max(agg_trade_id)                                    AS max_id,
                   sum(quantity)                                        AS quantity_sum,
                   sum(quantity) FILTER (WHERE NOT is_buyer_maker)      AS taker_buy_quantity_sum,
                   max(agg_trade_id) - min(agg_trade_id) + 1 - count(*) AS missing_ids_inside_day
            FROM {TRADES} WHERE {where} GROUP BY trade_date
        ),
        spark AS (SELECT * FROM {DAILY} WHERE {where})
        SELECT coalesce(d.trade_date, s.trade_date) AS trade_date
        FROM recount d FULL OUTER JOIN spark s ON d.trade_date = s.trade_date
        WHERE d.trade_date IS NULL OR s.trade_date IS NULL OR {differs}
        ORDER BY 1
        """
    ).fetchall()
    days = con.execute(f"SELECT count(*) FROM {DAILY} WHERE {where}").fetchone()
    assert days is not None
    return CheckResult(
        "daily_audit_recount",
        not mismatches and days[0] > 0,
        {"days": days[0], "mismatched_days": [str(r[0]) for r in mismatches][:20]},
    )


def _gaps_reference(
    con: duckdb.DuckDBPyConnection, where: str, symbol: str, month: date
) -> CheckResult:
    """[쓰임] 3 묶음(참고) — 깨끗한 체결을 번호 순으로 이웃 비교한 빈 곳 · 역행 수와 Spark 빈 곳
    표의 수."""
    row = con.execute(
        f"""
        WITH ordered AS (
            SELECT agg_trade_id, transact_time_ms,
                   lag(agg_trade_id)     OVER (ORDER BY agg_trade_id, transact_time_ms) AS prev_id,
                   lag(transact_time_ms) OVER (ORDER BY agg_trade_id, transact_time_ms) AS prev_ms
            FROM {TRADES} WHERE {where}
        )
        SELECT count(*) FILTER (WHERE agg_trade_id - prev_id > 1),
               coalesce(
                   sum(agg_trade_id - prev_id - 1) FILTER (WHERE agg_trade_id - prev_id > 1), 0
               ),
               count(*) FILTER (WHERE transact_time_ms < prev_ms)
        FROM ordered
        """
    ).fetchone()
    spark = con.execute(
        "SELECT count(*) FILTER (WHERE kind = 'gap'),"
        " count(*) FILTER (WHERE kind = 'time_reversed')"
        f" FROM {GAPS} WHERE symbol = '{symbol}' AND period_month = '{month:%Y-%m}'"
    ).fetchone()
    assert row is not None and spark is not None
    return CheckResult(
        "gaps_reference",
        None,
        {
            "duckdb_gaps": row[0],
            "duckdb_missing_ids": int(row[1]),
            "duckdb_time_reversed": row[2],
            "spark_gap_rows": spark[0],
            "spark_time_reversed_rows": spark[1],
        },
    )


def run_checks(con: duckdb.DuckDBPyConnection, symbol: str, month: date) -> list[CheckResult]:
    """[쓰임] 세 묶음을 모두 돌린다. 뷰(:func:`mdp.query.duck.register_views`)가 이미 있어야
    한다."""
    where = _month_filter(symbol, month)
    return [
        *_invariants(con, where),
        _daily_recount(con, where),
        _gaps_reference(con, where, symbol, month),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 — 검산하고 결과를 JSON 한 줄씩, 실패가 있으면 종료 코드 1.
    [이유] 종료 코드로 오케스트레이터(4-6)가 'Silver 검산 실패' 에서 다음 단계를 멈출 수 있게."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.query.check_silver")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    year_text, month_text = args.month.split("-")
    month = date(int(year_text), int(month_text), 1)

    def run(recorder: RunRecorder) -> bool:
        with record_run(
            recorder, "query.check_silver", {"symbol": args.symbol, "month": args.month}
        ) as rec:
            started = time.perf_counter()
            con = connect()
            log_json(event="views", registered=register_views(con))
            results = run_checks(con, args.symbol, month)
            elapsed = round(time.perf_counter() - started, 3)
            for result in results:
                log_json(event="check", name=result.name, passed=result.passed, **result.detail)
            failed = [r.name for r in results if r.passed is False]
            rows = next(r.detail["rows"] for r in results if r.name == "trade_rows")
            rec.add(
                Artifact(
                    "silver/agg_trades",
                    "silver",
                    "checked" if not failed else "check_failed",
                    row_count=int(str(rows)),
                    elapsed_s=elapsed,
                )
            )
            log_json(event="summary", failed=failed, elapsed_s=elapsed, run_id=str(rec.run_id))
            return not failed

    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            ok = run(recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        ok = run(NullRecorder())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
