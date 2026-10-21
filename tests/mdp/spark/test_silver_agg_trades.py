"""Silver 체결 감사 시험 — 결함을 일부러 넣은 작은 CSV 로 Spark 결과를 확인한다.

Spark 이미지 안에서만 돈다(호스트에는 pyspark 가 없어 건너뜀)::

    docker compose run --rm spark python -m pytest tests/mdp/spark -q

넣은 결함 (월 파일 2024-03):
- 머리줄, 형식이 틀린 줄 하나, 2024-04 로 넘어간 줄(기간 밖) 하나
- 빈 곳 103 · 104 (2 개 빠짐), 내용까지 같은 중복 105, 내용이 다른 중복 106
- 시각 역행 107 (106 보다 이른 시각), 마이크로초로 적힌 108
- 3 월 5 일 줄 200 · 201 — 그날은 일 파일(200 · 201 · 202)이 이긴다
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pyspark")

from mdp.bronze.archive import ArchiveFile, Dataset, Granularity
from mdp.silver.agg_trades import build_outputs, write_outputs
from mdp.silver.sources import SourceFile, SourcePlan
from mdp.spark.session import spark_session

MONTH = date(2024, 3, 1)
HEADER = "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"


def _ms(day: int, hour: int = 0, minute: int = 0, second: int = 0, month: int = 3) -> int:
    moment = datetime(2024, month, day, hour, minute, second, tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def _line(
    trade_id: int, ms: int, qty: str = "1.0", price: str = "100.0", maker: str = "false"
) -> str:
    return f"{trade_id},{price},{qty},{trade_id * 10},{trade_id * 10},{ms},{maker}"


MONTHLY_LINES = [
    HEADER,
    _line(100, _ms(1, 0, 0, 1), "2.0"),
    _line(101, _ms(1, 0, 0, 2), "1.5", maker="true"),
    _line(102, _ms(1, 0, 0, 3)),
    _line(105, _ms(1, 0, 0, 4)),  # 103 · 104 빠짐
    _line(105, _ms(1, 0, 0, 4)),  # 내용까지 같은 중복
    _line(106, _ms(1, 0, 0, 5)),
    _line(106, _ms(1, 0, 0, 5), price="101.0"),  # 내용이 다른 중복
    _line(107, _ms(1, 0, 0, 2)),  # 시각 역행
    _line(108, _ms(1, 1, 0, 0) * 1000),  # 마이크로초 표기, 01:00 정각 분
    "not,a,valid,row",
    _line(109, _ms(1, 0, 0, 1, month=4)),  # 기간 밖
    _line(200, _ms(5, 12)),
    _line(201, _ms(5, 12, 0, 1)),
]
DAILY_LINES = [HEADER, _line(200, _ms(5, 12)), _line(201, _ms(5, 12, 0, 1)), _line(202, _ms(5, 13))]


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    session = spark_session("test-silver")
    session.sparkContext.setLogLevel("ERROR")
    yield session


@pytest.fixture
def outputs(spark: Any, tmp_path: Path) -> Any:
    monthly = ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", MONTH)
    daily = ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2024, 3, 5), Granularity.DAILY)
    (tmp_path / "m.csv").write_text("\n".join(MONTHLY_LINES) + "\n", encoding="utf-8")
    (tmp_path / "d.csv").write_text("\n".join(DAILY_LINES) + "\n", encoding="utf-8")
    month_days = tuple(date(2024, 3, day) for day in range(1, 32) if day != 5)
    plan = SourcePlan(
        "BTCUSDT",
        MONTH,
        (
            SourceFile(monthly, "a" * 64, month_days),
            SourceFile(daily, "b" * 64, (date(2024, 3, 5),)),
        ),
    )
    kline_header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore"
    )
    (tmp_path / "k.csv").write_text(
        f"{kline_header}\n{_ms(1)},1,1,1,1,8.5,0,0,0,6.5,0,0\n{_ms(5)},1,1,1,1,4.0,0,0,0,4.0,0,0\n",
        encoding="utf-8",
    )
    return build_outputs(
        spark,
        plan,
        {monthly.relative_path: "m.csv", daily.relative_path: "d.csv"},
        [str(tmp_path / "m.csv"), str(tmp_path / "d.csv")],
        [str(tmp_path / "k.csv")],
        "run-1",
    )


def _by(df: Any, key: str) -> dict[Any, dict[str, Any]]:
    return {row[key]: row.asDict() for row in df.collect()}


def test_clean_trades_keep_one_of_exact_duplicates_and_drop_conflicts(outputs: Any) -> None:
    ids = sorted(row["agg_trade_id"] for row in outputs.trades.collect())
    assert ids == [100, 101, 102, 105, 107, 108, 200, 201, 202]
    by_id = _by(outputs.trades, "agg_trade_id")
    assert by_id[108]["transact_time_ms"] == _ms(1, 1)  # 마이크로초 → ms
    assert by_id[202]["source_path"].endswith("BTCUSDT-aggTrades-2024-03-05.zip")
    assert by_id[200]["source_path"].endswith("BTCUSDT-aggTrades-2024-03-05.zip")  # 일 파일이 이김
    assert by_id[100]["quantity"] == Decimal("2.00000000")


def test_quarantine_reasons(outputs: Any) -> None:
    reasons = sorted((row["reason"], row["agg_trade_id"]) for row in outputs.quarantine.collect())
    assert reasons == [
        ("conflicting_duplicate", 106),
        ("conflicting_duplicate", 106),
        ("outside_period", 109),
        ("parse_error", None),
    ]


def test_file_audit_counts_every_defect(outputs: Any) -> None:
    audit = _by(outputs.audit_file, "granularity")
    monthly = audit["monthly"]
    assert monthly["header_rows"] == 1 and monthly["parse_error"] == 1
    assert (
        monthly["rows"] == 12 and monthly["duplicate_rows"] == 2
    )  # 읽힌 줄 12, 105 · 106 한 번씩 더
    assert monthly["gap_count"] == 2 and monthly["missing_ids"] == 2 + 90  # 103·104, 110~199
    assert monthly["time_reversed_rows"] == 2  # 106 → 107, 109(4 월) → 200(3 월 5 일)
    assert monthly["outside_period"] == 1 and monthly["conflicting_duplicate"] == 2
    assert monthly["replaced_rows"] == 2  # 3 월 5 일 줄은 일 파일에 넘김
    assert monthly["time_units"] == ["ms", "us"]
    assert audit["daily"]["rows"] == 3 and audit["daily"]["gap_count"] == 0


def test_gaps_table_has_positions(outputs: Any) -> None:
    gaps = sorted(
        (row["kind"], row["prev_id"], row["next_id"], row["missing_ids"])
        for row in outputs.gaps.collect()
        if row["source_path"].endswith("2024-03.zip")
    )
    assert ("gap", 102, 105, 2) in gaps and ("time_reversed", 106, 107, 0) in gaps


def test_daily_audit_compares_with_klines(outputs: Any) -> None:
    daily = _by(outputs.audit_daily, "trade_date")
    first = daily[date(2024, 3, 1)]
    # 3 월 1 일 깨끗한 체결: 2.0 + 1.5 + 1 + 1(105) + 1(107) + 1(108) = 7.5 · 1 분봉 8.5 → 의심
    assert first["quantity_sum"] == Decimal("7.50000000") and first["suspect"] is True
    assert first["missing_ids_inside_day"] == 3  # 100~108 중 103 · 104 · 106
    fifth = daily[date(2024, 3, 5)]
    assert fifth["rows"] == 3 and fifth["kline_volume"] == Decimal("4.00000000")
    assert fifth["volume_diff_pct"] == pytest.approx(25.0) and fifth["suspect"] is True


def test_check_minutes_match_research_definition(outputs: Any) -> None:
    minutes = {
        (row["source_path"].endswith("2024-03.zip"), row["minute_ms"]): row
        for row in outputs.check_minutes.collect()
    }
    first = minutes[(True, _ms(1, 0))]
    # 00:00 분 — 파일 안 모든 읽힌 줄(중복 · 역행 포함, 연구 216 추출과 같은 정의)
    assert first["trades"] == 8 and first["taker_buy_quantity"] == Decimal("8.00000000")
    assert minutes[(True, _ms(1, 1))]["trades"] == 1


def test_write_is_idempotent_by_month(
    outputs: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MDP_LAKE_ROOT", str(tmp_path / "lake"))
    first = write_outputs(outputs, "BTCUSDT", MONTH)
    second = write_outputs(outputs, "BTCUSDT", MONTH)
    assert first["agg_trades"][1] == second["agg_trades"][1] == 9
    assert second["agg_trades"][0] == first["agg_trades"][0] + 1  # 새 버전, 같은 내용
