"""스트림 1 분봉 시험 — 실제 Structured Streaming 으로 워터마크 · 중복 제거 · 늦은 체결 버림 ·
배치와 같은 봉.

Kafka 대신 파일 흐름(파일 하나 = 묶음 하나)을 원천으로 쓴다 — 처리 코드(:func:`parse_raw` ·
:func:`stream_bars`)는 Kafka 일 때와 같다. Spark 이미지 안에서만 돈다.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pyspark")

from mdp.gold.bars_1m import BAR_COLUMNS, build_bars
from mdp.spark.session import spark_session
from mdp.stream.bars_1m import parse_raw, stream_bars
from mdp.stream.messages import replay_message

T0 = 1_709_251_200_000  # 2024-03-01 00:00 UTC


def _row(agg_id: int, price: str, qty: str, time_ms: int, maker: bool) -> list[str]:
    first = agg_id * 10
    return [str(agg_id), price, qty, str(first), str(first + 1), str(time_ms), str(maker).lower()]


TRADES = {
    11: _row(11, "100.0", "1.0", T0 + 5, False),
    12: _row(12, "105.5", "2.0", T0 + 30_000, True),
    13: _row(13, "99.0", "0.5", T0 + 60_001, False),  # 00:01
    14: _row(14, "101.0", "1.5", T0 + 180_000, True),  # 00:03 — 워터마크를 민다
    16: _row(16, "102.0", "1.0", T0 + 240_000, False),  # 00:04 — 한 번 더 민다
    15: _row(15, "50.0", "9.0", T0 + 20, False),  # 00:00 에 속하지만 너무 늦게 온다
}
BATCHES = [
    [11, 12, 13, 11],  # 11 은 두 번 (중복)
    [14],  # 다음 묶음의 워터마크 = 00:03 − 1 분 = 00:02 → 00:00 · 00:01 봉 확정
    [16],
    # 늦은 체결은 '바로 앞 묶음의' 워터마크(00:02)와 비교된다 — Spark 는 상태 연산자가 여럿이면
    # 늦음 판정에 한 묶음 전 워터마크를 쓴다. 그래서 [14] 바로 다음이 아니라 한 묶음 뒤에 보낸다.
    [15],
]


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    session = spark_session("test-stream")
    session.sparkContext.setLogLevel("ERROR")
    yield session


def _write_batches(folder: Path) -> None:
    folder.mkdir()
    for index, ids in enumerate(BATCHES):
        lines = [replay_message("BTCUSDT", TRADES[i])[1].decode() for i in ids]
        path = folder / f"{index:03d}.json"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # 파일 흐름은 고친 시각 순으로 묶음을 만든다 — 한꺼번에 쓰면 시각이 같아 순서가 섞인다
        os.utime(path, (1_700_000_000 + index * 10, 1_700_000_000 + index * 10))


def test_stream_bars_match_batch_and_drop_late(spark: Any, tmp_path: Path) -> None:
    from pyspark.sql import functions as F

    source = tmp_path / "raw"
    _write_batches(source)
    raw = (
        spark.readStream.format("text")
        .option("maxFilesPerTrigger", 1)
        .load(str(source))
        .withColumn("timestamp", F.timestamp_millis(F.lit(T0)))  # Kafka 의 '받은 시각' 자리
    )
    collected: list[Any] = []
    query = (
        stream_bars(parse_raw(raw), "1 minute")
        .writeStream.outputMode("append")
        .foreachBatch(lambda batch, _id: collected.extend(batch.collect()))
        .option("checkpointLocation", str(tmp_path / "checkpoint"))
        .trigger(availableNow=True)
        .start()
    )
    query.awaitTermination()
    dropped = sum(
        int(op.get("numRowsDroppedByWatermark", 0))
        for p in query.recentProgress
        for op in p.get("stateOperators", [])
    )

    bars = {r["bar_start_ms"]: r for r in collected}
    # 00:00 · 00:01 은 확정, 00:03 · 00:04 는 워터마크가 아직 안 지나 내보내지 않음
    assert sorted(bars) == [T0, T0 + 60_000]
    assert dropped >= 1

    # 같은 체결(중복 · 늦은 것 뺌)로 배치 Gold 를 만들면 칸마다 같아야 한다
    schema = (
        "symbol string, agg_trade_id long, price decimal(18,8), quantity decimal(18,8),"
        " first_trade_id long, last_trade_id long, transact_time_ms long, is_buyer_maker boolean"
    )
    kept = [
        (
            "BTCUSDT",
            int(r[0]),
            Decimal(r[1]),
            Decimal(r[2]),
            int(r[3]),
            int(r[4]),
            int(r[5]),
            r[6] == "true",
        )
        for i, r in TRADES.items()
        if i in (11, 12, 13)
    ]
    batch = {
        r["bar_start_ms"]: r
        for r in build_bars(spark.createDataFrame(kept, schema), 0, "r").collect()
    }
    for minute, bar in bars.items():
        assert {c: bar[c] for c in BAR_COLUMNS} == {c: batch[minute][c] for c in BAR_COLUMNS}
    assert bars[T0]["agg_trade_count"] == 2  # 중복 11 은 한 번만
    assert bars[T0]["low"] == Decimal("100")  # 늦게 온 50.0 은 들어가지 않음
    assert bars[T0]["ids_contiguous"] is True  # 11, 12 이어짐
