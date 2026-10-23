"""벤치마크 시험 — 작은 파일에서 연구 추출기 · DuckDB 가 손으로 계산한 정답과 같은지, 비교 규칙."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mdp.bench.engines import run_legacy
from mdp.bench.result import compare
from mdp.runlog.recorder import REPO_ROOT
from tests.mdp.bench_fixture import CSV_TEXT, EXPECTED


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    csv_path = tmp_path / "BTCUSDT-aggTrades-2024-03.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")
    zip_path = tmp_path / "BTCUSDT-aggTrades-2024-03.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(csv_path.name, CSV_TEXT)
    return zip_path, csv_path


def test_legacy_full_matches_hand_answer(files: tuple[Path, Path]) -> None:
    result = run_legacy(files[0], full=True, repo_root=REPO_ROOT)
    assert result["duplicate_rows"] is None  # 줄 순서로 세는 연구 추출기는 중복 줄 수를 못 낸다
    assert compare(EXPECTED, result) == []


def test_legacy_fast_runs_and_counts_rows(files: tuple[Path, Path]) -> None:
    result = run_legacy(files[0], full=False, repo_root=REPO_ROOT)
    assert result["rows"] == 8  # 나머지 값은 실제 비교 보고에서 정답지와 맞대 본다


def test_duckdb_matches_hand_answer(files: tuple[Path, Path]) -> None:
    pytest.importorskip("duckdb")
    from mdp.bench.engines import run_duckdb

    assert compare(EXPECTED, run_duckdb(files[1], threads=2, memory_limit="1GB")) == []


def test_compare_reports_differences_and_skips_uncomputed_values() -> None:
    other = {
        **EXPECTED,
        "rows": 9,
        "duplicate_rows": None,
        "windows": {**EXPECTED["windows"], str(1): ["1", "1", 1]},
    }
    differences = compare(EXPECTED, other)
    assert differences[0] == "rows: 8 != 9"
    assert not any(d.startswith("duplicate_rows") for d in differences)
    assert any(d.startswith("windows[1]") for d in differences)
    near = {
        **EXPECTED,
        "check_minutes": {
            **EXPECTED["check_minutes"],
            str(1_709_251_200_000): [8.500000000000002, 6.5, 6],
        },
    }
    assert compare(EXPECTED, near) == []  # float 끝자리 차이는 같다고 본다
