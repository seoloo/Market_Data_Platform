"""벤치마크 Spark 엔진 시험 — 손으로 계산한 정답과 같은지 (Spark 이미지 안에서만 돈다)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyspark")

from mdp.bench.engines import run_spark
from mdp.bench.result import compare
from tests.mdp.bench_fixture import CSV_TEXT, EXPECTED


def test_spark_matches_hand_answer(tmp_path: Path) -> None:
    csv_path = tmp_path / "trades.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")
    assert compare(EXPECTED, run_spark(str(csv_path))) == []
