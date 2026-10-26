"""국내 주식 스트림 1분봉 · 매일 대사 시험.

체결 글자 → 봉, 중복 · 반쪽 메시지 · 누적 이음, 대사 원인 다섯 가지. Spark 이미지 안에서만 돈다.
스트림 함수는 배치 DataFrame 에도 같은 결과를 낸다(워터마크만 쓰지 않음).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pyspark")

from mdp.kis.realtime import FIELD, TRADE_COLUMNS
from mdp.reconcile.kis_daily import classify_minutes, summarize
from mdp.spark.session import spark_session
from mdp.stream.kis_bars import kis_stream_bars, parse_kis_raw


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    session = spark_session("test-kis-stream")
    session.sparkContext.setLogLevel("ERROR")
    yield session


def rec(hour: str, price: int, volume: int, cumulative: int, symbol: str = "005930") -> list[str]:
    values = ["0"] * len(TRADE_COLUMNS)
    values[FIELD["MKSC_SHRN_ISCD"]] = symbol
    values[FIELD["STCK_CNTG_HOUR"]] = hour
    values[FIELD["STCK_PRPR"]] = str(price)
    values[FIELD["CNTG_VOL"]] = str(volume)
    values[FIELD["ACML_VOL"]] = str(cumulative)
    values[FIELD["BSOP_DATE"]] = "20261027"
    return values


def msg(*records: list[str], count: int | None = None) -> str:
    n = len(records) if count is None else count
    return f"0|H0STCNT0|{n:03d}|" + "^".join(v for r in records for v in r)


def _kafka(spark: Any, texts: list[str]) -> Any:
    received = datetime(2026, 10, 27, 0, 31, tzinfo=UTC).replace(tzinfo=None)  # Spark 는 UTC
    return spark.createDataFrame(
        [(t.encode("utf-8"), received) for t in texts], "value BINARY, timestamp TIMESTAMP"
    )


def test_frames_become_trades_and_broken_ones_are_dropped(spark: Any) -> None:
    good = msg(rec("093001", 100, 5, 1005), rec("093002", 101, 3, 1008))
    texts = [good, msg(rec("093003", 102, 1, 1009), count=2), "1|H0STCNI0|001|x", "garbage"]
    trades = parse_kis_raw(_kafka(spark, texts)).collect()
    assert [(t["trade_time"], t["price"], t["cumulative_volume"]) for t in trades] == [
        ("093001", Decimal("100.00"), 1005),
        ("093002", Decimal("101.00"), 1008),
    ]
    utc = datetime(2026, 10, 27, 0, 30, 1, tzinfo=UTC).replace(tzinfo=None)
    assert trades[0]["event_time"] == utc  # 서울 09:30:01 = UTC 00:30:01
    assert trades[0]["trade_date"] == date(2026, 10, 27)


def test_bars_dedup_by_cumulative_volume_and_check_continuity(spark: Any) -> None:
    texts = [
        msg(rec("093001", 100, 5, 1005), rec("093030", 104, 2, 1007)),
        msg(rec("093030", 104, 2, 1007)),  # 다시 온 같은 체결
        msg(rec("093059", 102, 3, 1010)),
        msg(rec("093105", 99, 4, 1014), rec("093150", 98, 1, 1020)),  # 1015 ~ 1019 를 놓침
    ]
    bars = {
        r["bar_time"]: r
        for r in kis_stream_bars(parse_kis_raw(_kafka(spark, texts)), "2 minutes").collect()
    }
    first = bars["093000"]
    assert (first["open"], first["high"], first["low"], first["close"]) == tuple(
        Decimal(x) for x in ("100.00", "104.00", "100.00", "102.00")
    )
    assert (first["volume"], first["trade_count"], first["acml_contiguous"]) == (10, 3, True)
    assert (first["first_cumulative"], first["first_trade_volume"]) == (1005, 5)
    assert bars["093100"]["acml_contiguous"] is False
    assert str(first["trade_date"]) == "2026-10-27"


def _stream_bar(
    bar_time: str,
    ohlcv: tuple[int, int, int, int, int],
    first_cum: int,
    last_cum: int,
    first_vol: int,
    contiguous: bool = True,
) -> tuple[Any, ...]:
    o, h, low, c, v = ohlcv
    return (
        "005930",
        date(2026, 10, 27),
        bar_time,
        *(Decimal(x) for x in (o, h, low, c)),
        v,
        1,
        first_cum,
        last_cum,
        first_vol,
        contiguous,
        0,
    )


STREAM_SCHEMA = (
    "symbol STRING, trade_date DATE, bar_time STRING, open DECIMAL(18,2), high DECIMAL(18,2),"
    " low DECIMAL(18,2), close DECIMAL(18,2), volume BIGINT, trade_count BIGINT,"
    " first_cumulative BIGINT, last_cumulative BIGINT, first_trade_volume BIGINT,"
    " acml_contiguous BOOLEAN, last_received_ms BIGINT"
)
BATCH_SCHEMA = (
    "symbol STRING, trade_date DATE, bar_time STRING, session STRING, open DECIMAL(18,2),"
    " high DECIMAL(18,2), low DECIMAL(18,2), close DECIMAL(18,2), volume BIGINT"
)


def test_reconcile_names_each_cause(spark: Any) -> None:
    stream = spark.createDataFrame(
        [
            _stream_bar("090000", (100, 101, 99, 100, 10), 10, 19, 10),  # 같음
            _stream_bar("090100", (100, 100, 100, 100, 5), 25, 29, 1),  # 앞 분(…19)과 이음 끊김
            _stream_bar("090200", (100, 102, 100, 101, 4), 30, 35, 1, False),  # 안에서 끊김
            _stream_bar("090300", (101, 101, 101, 101, 2), 36, 37, 1),  # 이어지는데 값 다름
            _stream_bar("090500", (101, 101, 101, 101, 1), 40, 40, 1),  # 배치에 없음
        ],
        STREAM_SCHEMA,
    )

    def b(
        bar_time: str, ohlcv: tuple[int, int, int, int, int], session: str = "regular"
    ) -> tuple[Any, ...]:
        o, h, low, c, v = ohlcv
        return (
            "005930",
            date(2026, 10, 27),
            bar_time,
            session,
            *(Decimal(x) for x in (o, h, low, c)),
            v,
        )

    batch = spark.createDataFrame(
        [
            b("090000", (100, 101, 99, 100, 10)),
            b("090100", (100, 100, 100, 100, 9)),
            b("090200", (100, 102, 100, 101, 6)),
            b("090300", (101, 102, 101, 101, 2)),
            b("090400", (101, 101, 101, 101, 3)),  # 스트림에 없음
            b("160000", (101, 101, 101, 101, 3), session="after_hours"),  # 장 후는 맞대지 않음
        ],
        BATCH_SCHEMA,
    )
    minutes = classify_minutes(stream, batch, "r")
    got = {r["bar_time"]: r["status"] for r in minutes.collect()}
    assert got == {
        "090000": "matched",
        "090100": "missed_trades",
        "090200": "missed_trades",
        "090300": "values_differ",
        "090400": "stream_missing",
        "090500": "only_stream",
    }
    [day] = summarize(minutes, "r").collect()
    assert (day["minutes"], day["matched"], day["missed_trades"], day["stream_missing"]) == (
        6,
        1,
        2,
        1,
    )
    assert (day["stream_first"], day["stream_last"]) == ("090000", "090500")
    assert (day["stream_volume"], day["batch_volume"]) == (22, 30)
