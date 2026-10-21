"""Silver 출처 고르기 · 검증 · 풀기 시험 (Spark 없이)."""

from __future__ import annotations

import hashlib
import zipfile
from datetime import date
from pathlib import Path

import pytest

from mdp.bronze.archive import ArchiveFile, Dataset, Granularity
from mdp.errors import DataIntegrityError
from mdp.silver.sources import plan_month_sources, stage_csv, verify_bronze_file

MONTH = date(2024, 3, 1)
MONTHLY = ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", MONTH)


def _daily(day: int) -> ArchiveFile:
    return ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2024, 3, day), Granularity.DAILY)


def test_monthly_file_covers_every_day_when_no_daily_files() -> None:
    plan = plan_month_sources({MONTHLY.relative_path: (1, "a" * 64)}, "BTCUSDT", MONTH)
    assert len(plan.sources) == 1
    assert plan.sources[0].file == MONTHLY and len(plan.sources[0].days) == 31
    assert plan.days_covered[0] == date(2024, 3, 1) and plan.days_covered[-1] == date(2024, 3, 31)


def test_daily_file_wins_its_day_over_monthly() -> None:
    manifest = {
        MONTHLY.relative_path: (1, "a" * 64),
        _daily(5).relative_path: (1, "b" * 64),
    }
    plan = plan_month_sources(manifest, "BTCUSDT", date(2024, 3, 17))
    monthly, daily = plan.sources
    assert date(2024, 3, 5) not in monthly.days and len(monthly.days) == 30
    assert (
        daily.file == _daily(5) and daily.days == (date(2024, 3, 5),) and daily.sha256 == "b" * 64
    )
    assert len(plan.days_covered) == 31


def test_only_daily_files_when_monthly_missing_and_nothing_when_empty() -> None:
    manifest = {_daily(1).relative_path: (1, "c" * 64), _daily(2).relative_path: (1, "d" * 64)}
    plan = plan_month_sources(manifest, "BTCUSDT", MONTH)
    assert [s.file.period.day for s in plan.sources] == [1, 2]
    assert plan_month_sources({}, "BTCUSDT", MONTH).sources == ()


def test_klines_plan_uses_interval() -> None:
    klines = ArchiveFile(Dataset.KLINES, "BTCUSDT", MONTH, interval="1m")
    plan = plan_month_sources(
        {klines.relative_path: (1, "e" * 64)}, "BTCUSDT", MONTH, Dataset.KLINES, "1m"
    )
    assert plan.sources[0].file == klines


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


def test_verify_and_stage(tmp_path: Path) -> None:
    zip_path = _zip(
        tmp_path / "BTCUSDT-aggTrades-2024-03.zip", {"BTCUSDT-aggTrades-2024-03.csv": b"1,2\n"}
    )
    verify_bronze_file(zip_path, hashlib.sha256(zip_path.read_bytes()).hexdigest())
    with pytest.raises(DataIntegrityError, match="체크섬 목록과 다르다"):
        verify_bronze_file(zip_path, "0" * 64)

    staged = stage_csv(zip_path, tmp_path / "staging")
    assert staged.read_bytes() == b"1,2\n"
    assert stage_csv(zip_path, tmp_path / "staging") == staged  # 이미 있으면 그대로
    assert not list((tmp_path / "staging").glob("*.part"))


def test_stage_refuses_unexpected_members(tmp_path: Path) -> None:
    wrong = _zip(
        tmp_path / "BTCUSDT-aggTrades-2024-03.zip", {"ETHUSDT-aggTrades-2024-03.csv": b"x"}
    )
    with pytest.raises(DataIntegrityError, match="예상과 다르다"):
        stage_csv(wrong, tmp_path / "staging")
