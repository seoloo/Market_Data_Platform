"""DuckDB 조회 층 시험 — 작은 표를 DuckDB 안에 직접 만들어, 검산이 틀린 데이터를 잡는지 본다.

실제 레이크(``data/lake/silver``)가 있으면 그 한 달치로도 한 번 돈다(없으면 건너뜀).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from mdp.query.check_silver import run_checks  # noqa: E402
from mdp.query.duck import connect, delta_source, register_views  # noqa: E402

MONTH = date(2024, 3, 1)


def _ms(day: int, second: int) -> int:
    return int(datetime(2024, 3, day, 0, 0, second, tzinfo=UTC).timestamp() * 1000)


TRADES = [  # (번호, 가격, 수량, 시각, is_buyer_maker, 날짜)
    (100, "10.0", "1.5", _ms(1, 1), False, date(2024, 3, 1)),
    (101, "10.0", "0.5", _ms(1, 2), True, date(2024, 3, 1)),
    (103, "10.0", "2.0", _ms(1, 3), False, date(2024, 3, 1)),  # 102 빠짐
    (104, "10.0", "1.0", _ms(2, 1), False, date(2024, 3, 2)),
]


def _make(
    con: Any, trades: list[tuple[Any, ...]], daily_override: dict[str, Any] | None = None
) -> None:
    """뷰 대신 같은 이름의 표를 만든다 — 검산 SQL 은 이름만 본다."""
    con.execute(
        "CREATE OR REPLACE TABLE silver_agg_trades (symbol VARCHAR, trade_date DATE,"
        " agg_trade_id BIGINT,"
        " price DECIMAL(18,8), quantity DECIMAL(18,8), first_trade_id BIGINT, last_trade_id BIGINT,"
        " transact_time_ms BIGINT, is_buyer_maker BOOLEAN)"
    )
    for trade_id, price, qty, ms, maker, day in trades:
        con.execute(
            "INSERT INTO silver_agg_trades VALUES ('BTCUSDT', ?, ?, ?, ?, ?, ?, ?, ?)",
            [day, trade_id, price, qty, trade_id * 10, trade_id * 10, ms, maker],
        )
    con.execute(
        """CREATE OR REPLACE TABLE silver_agg_trades_audit_daily AS
           SELECT symbol, trade_date, count(*) AS rows, min(agg_trade_id) AS min_id,
                  max(agg_trade_id) AS max_id, sum(quantity) AS quantity_sum,
                  sum(quantity) FILTER (WHERE NOT is_buyer_maker) AS taker_buy_quantity_sum,
                  max(agg_trade_id) - min(agg_trade_id) + 1 - count(*) AS missing_ids_inside_day
           FROM silver_agg_trades GROUP BY ALL"""
    )
    if daily_override:
        sets = ", ".join(f"{k} = {v}" for k, v in daily_override.items() if k != "where")
        con.execute(
            f"UPDATE silver_agg_trades_audit_daily SET {sets} WHERE {daily_override['where']}"
        )
    con.execute(
        "CREATE OR REPLACE TABLE silver_agg_trades_gaps AS SELECT 'BTCUSDT' AS symbol,"
        " '2024-03' AS period_month, 'gap' AS kind"
    )


@pytest.fixture
def con() -> Iterator[Any]:
    connection = duckdb.connect()
    yield connection
    connection.close()


def _by_name(results: list[Any]) -> dict[str, Any]:
    return {r.name: r for r in results}


def test_clean_data_passes_and_gap_reference_is_counted(con: Any) -> None:
    _make(con, TRADES)
    results = _by_name(run_checks(con, "BTCUSDT", MONTH))
    assert all(r.passed is not False for r in results.values())
    assert results["trade_rows"].detail["rows"] == 4
    gaps = results["gaps_reference"].detail
    assert (
        gaps["duckdb_gaps"] == 1 and gaps["duckdb_missing_ids"] == 1 and gaps["spark_gap_rows"] == 1
    )


def test_duplicate_id_and_wrong_date_are_caught(con: Any) -> None:
    bad = [*TRADES, (104, "10.0", "1.0", _ms(2, 1), False, date(2024, 3, 2))]  # 104 두 번
    bad[0] = (100, "10.0", "1.5", _ms(1, 1), False, date(2024, 3, 2))  # 3/1 체결을 3/2 로
    _make(con, bad)
    results = _by_name(run_checks(con, "BTCUSDT", MONTH))
    assert (
        results["duplicate_ids"].passed is False
        and results["duplicate_ids"].detail["violations"] == 1
    )
    assert results["wrong_date"].passed is False and results["wrong_date"].detail["violations"] == 1


def test_one_satoshi_difference_in_spark_audit_is_caught(con: Any) -> None:
    _make(
        con,
        TRADES,
        {"quantity_sum": "quantity_sum + 0.00000001", "where": "trade_date = DATE '2024-03-02'"},
    )
    result = _by_name(run_checks(con, "BTCUSDT", MONTH))["daily_audit_recount"]
    assert result.passed is False and result.detail["mismatched_days"] == ["2024-03-02"]


def test_day_missing_from_spark_audit_is_caught(con: Any) -> None:
    _make(con, TRADES)
    con.execute("DELETE FROM silver_agg_trades_audit_daily WHERE trade_date = DATE '2024-03-01'")
    result = _by_name(run_checks(con, "BTCUSDT", MONTH))["daily_audit_recount"]
    assert result.passed is False and result.detail["mismatched_days"] == ["2024-03-01"]


def test_other_month_is_ignored(con: Any) -> None:
    _make(con, TRADES)
    result = _by_name(run_checks(con, "BTCUSDT", date(2024, 4, 1)))
    assert result["trade_rows"].passed is False and result["trade_rows"].detail["rows"] == 0


def test_delta_source_quotes_path_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MDP_LAKE_ROOT", str(tmp_path / "it's lake"))
    plain = delta_source("silver", "agg_trades")
    assert plain.startswith("delta_scan('") and "it''s lake" in plain and "\\" not in plain
    assert delta_source("silver", "agg_trades", 3).endswith("version => 3)")
    with pytest.raises(ValueError):
        delta_source("bronze", "x")


def test_register_views_skips_folders_that_are_not_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, con: Any
) -> None:
    monkeypatch.setenv("MDP_LAKE_ROOT", str(tmp_path))
    (tmp_path / "silver" / "_reports").mkdir(parents=True)
    assert register_views(con) == []


REAL_TABLE = Path(__file__).resolve().parents[2] / "data" / "lake" / "silver" / "agg_trades"


@pytest.mark.skipif(not (REAL_TABLE / "_delta_log").is_dir(), reason="실제 Silver 표 없음")
def test_real_lake_month_passes() -> None:
    real = connect()
    register_views(real)
    results = run_checks(real, "BTCUSDT", MONTH)
    assert [r.name for r in results if r.passed is False] == []
