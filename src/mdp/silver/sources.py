"""Silver 가 읽을 Bronze 파일 고르기 · 검증 · 풀기 (Spark 없이 도는 부분).

언제 쓰나: Silver 작업(:mod:`mdp.silver.agg_trades`)이 Spark 로 읽기 전에.
읽고 쓰는 것: 체크섬 목록(읽기) · Bronze zip(읽기, SHA-256 다시 계산) → 임시 폴더의 CSV (작업 뒤
              지움).

규칙 (한 달치를 만들 때):
- 그 달 월 파일이 기본 출처다.
- 그 달 어느 날의 **일 파일이 Bronze 에 있으면 그날은 일 파일이 이긴다** — 일 파일은 사람이
  ``sync --days`` 로 일부러 받은 '교체본' 이기 때문이다
  (연구 216 에서 월 파일이 잘린 날을 일 파일로 바꾼 방식).
- 월 파일이 없으면 그 달의 일 파일들만 쓴다(이번 달 · 지난달 월 파일이 아직 안 올라온 경우).

[이유] Bronze 는 원본을 다 보관하고, '어느 원본을 쓸지' 는 Silver 가 정한다. 규칙을 이 파일 하나에
       적어 두면 같은 Bronze 에서 몇 번 다시 만들어도 같은 Silver 가 나온다.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from mdp.bronze.archive import ArchiveFile, Dataset, Granularity
from mdp.errors import DataIntegrityError

__all__ = ["SourceFile", "SourcePlan", "plan_month_sources", "stage_csv", "verify_bronze_file"]


@dataclass(frozen=True, slots=True)
class SourceFile:
    """[쓰임] Silver 에 들어갈 Bronze 파일 하나 — 이름표 · 체크섬 목록의 SHA-256 · 이 파일이 맡은
    날들."""

    file: ArchiveFile
    sha256: str
    days: tuple[date, ...]
    """이 파일에서 Silver 로 가져갈 날. 월 파일이면 일 파일에 넘긴 날은 빠진다."""


@dataclass(frozen=True, slots=True)
class SourcePlan:
    """[쓰임] 한 달치 Silver 를 만들 출처 계획."""

    symbol: str
    month: date
    sources: tuple[SourceFile, ...]

    @property
    def days_covered(self) -> tuple[date, ...]:
        """[쓰임] 출처가 하나라도 있는 날 (정렬)."""
        return tuple(sorted({day for source in self.sources for day in source.days}))


def _month_days(month: date) -> list[date]:
    """[쓰임] 그 달의 모든 날."""
    next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return [month + timedelta(days=offset) for offset in range((next_month - month).days)]


def plan_month_sources(
    manifest: Mapping[str, tuple[int, str]],
    symbol: str,
    month: date,
    dataset: Dataset = Dataset.AGG_TRADES,
    interval: str | None = None,
) -> SourcePlan:
    """[쓰임] 체크섬 목록(= Bronze 에 있는 것)만 보고 한 달치 출처를 정한다 (위 규칙).
    [이유] 디스크를 뒤지지 않고 목록만 본다 — 목록에 없는 파일은 검증된 적이 없으므로 쓰지
           않는다."""
    month = month.replace(day=1)
    monthly = ArchiveFile(dataset, symbol, month, Granularity.MONTHLY, interval)
    daily_by_day = {
        day: ArchiveFile(dataset, symbol, day, Granularity.DAILY, interval)
        for day in _month_days(month)
    }
    daily_present = {
        day: file for day, file in daily_by_day.items() if file.relative_path in manifest
    }
    sources: list[SourceFile] = []
    if monthly.relative_path in manifest:
        monthly_days = tuple(day for day in _month_days(month) if day not in daily_present)
        if monthly_days:
            sources.append(SourceFile(monthly, manifest[monthly.relative_path][1], monthly_days))
    for day, file in sorted(daily_present.items()):
        sources.append(SourceFile(file, manifest[file.relative_path][1], (day,)))
    return SourcePlan(symbol, month, tuple(sources))


def verify_bronze_file(path: Path, expected_sha256: str) -> None:
    """[쓰임] Silver 가 읽기 직전에 Bronze 파일의 SHA-256 을 다시 계산해 체크섬 목록과 대조한다.
    [이유] 받은 뒤 디스크에서 바뀌었을 수도 있다. 검증 안 된 원본으로 Silver 를 만들지 않는다."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    if digest.hexdigest() != expected_sha256:
        raise DataIntegrityError(f"Bronze 파일이 체크섬 목록과 다르다: {path}")


def stage_csv(zip_path: Path, staging_dir: Path) -> Path:
    """[쓰임] zip 안의 CSV 하나를 임시 폴더로 풀어 그 경로를 돌려준다(이미 같은 크기로 있으면
              그대로).
    [이유] Spark 는 zip 을 바로 못 읽는다. 1 MiB 씩 흘려 풀어 메모리는 작게 쓴다. CSV 가 정확히
           하나이고 이름이 zip 과 같아야 한다 — 엉뚱한 내용물을 걸러낸다."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        expected = zip_path.with_suffix(".csv").name
        if names != [expected]:
            raise DataIntegrityError(f"zip 안 CSV 가 예상과 다르다: {names} (기대 {expected})")
        target = staging_dir / expected
        info = archive.getinfo(expected)
        if target.exists() and target.stat().st_size == info.file_size:
            return target
        staging_dir.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        with archive.open(expected) as source, partial.open("wb") as sink:
            shutil.copyfileobj(source, sink, 1 << 20)
        partial.replace(target)
    return target
