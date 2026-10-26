"""받아 둔 자료 들여오기 시험 — 그대로 복사 · 실행 기록 빼기 · 멱등 · 바뀐 파일 감지 · 반쪽 없음."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdp.bronze import imported
from mdp.bronze.fetch import read_manifest
from mdp.bronze.imported import SUCCESS, ImportStatus, import_folder
from mdp.errors import BronzeConflictError, DataIntegrityError


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "kr_stocks"
    (source / "prices").mkdir(parents=True)
    (source / "prices" / "005930.csv").write_bytes(b"date,close\r\n20070102,6410\r\n")
    (source / "info.json").write_text('{"005930": {"name": "삼성전자"}}', encoding="utf-8")
    (source / "prices.log").write_text("실행 기록", encoding="utf-8")
    return source


def test_copies_bytes_exactly_and_skips_logs(tmp_path: Path) -> None:
    source = _source(tmp_path)
    result = import_folder(source, tmp_path / "bronze", "auto_trading/research/kr_stocks")
    target = tmp_path / "bronze" / "imported" / "auto_trading" / "research" / "kr_stocks"
    assert result.status is ImportStatus.COPIED and result.files == 2
    assert (target / "prices" / "005930.csv").read_bytes() == b"date,close\r\n20070102,6410\r\n"
    assert not (target / "prices.log").exists()
    marker = json.loads((target / SUCCESS).read_text(encoding="utf-8"))
    assert [f["path"] for f in marker["files"]] == ["info.json", "prices/005930.csv"]
    assert {p for p, _, _ in result.rows} == {
        "imported/auto_trading/research/kr_stocks/info.json",
        "imported/auto_trading/research/kr_stocks/prices/005930.csv",
        f"imported/auto_trading/research/kr_stocks/{SUCCESS}",
    }


def test_second_import_verifies_and_tampering_stops(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = import_folder(source, tmp_path / "bronze", "x")
    again = import_folder(source, tmp_path / "bronze", "x")
    assert again.status is ImportStatus.ALREADY_PRESENT and again.rows == first.rows
    (tmp_path / "bronze" / "imported" / "x" / "info.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BronzeConflictError):
        import_folder(source, tmp_path / "bronze", "x")


def test_leftover_folder_without_marker_stops(tmp_path: Path) -> None:
    (tmp_path / "bronze" / "imported" / "x").mkdir(parents=True)
    with pytest.raises(BronzeConflictError):
        import_folder(_source(tmp_path), tmp_path / "bronze", "x")


def test_empty_source_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(DataIntegrityError):
        import_folder(tmp_path / "empty", tmp_path / "bronze", "x")
    assert not (tmp_path / "bronze" / "imported").exists()


def test_cli_writes_manifest(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manifest = tmp_path / "imported.csv"
    args = [
        "--source",
        str(source),
        "--name",
        "x",
        "--root",
        str(tmp_path / "bronze"),
        "--manifest",
        str(manifest),
    ]
    assert imported.main(args) == 0
    assert len(read_manifest(manifest)) == 3
    before = manifest.read_bytes()
    assert imported.main(args) == 0
    assert manifest.read_bytes() == before
