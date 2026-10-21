"""증분 받기 · 백필 시험 — 계획(순수 함수)과 가짜 원천으로 건너뛰기 · 일 파일 대체 · 멱등을 본다."""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest

from mdp.bronze.archive import ArchiveFile, Dataset, Granularity
from mdp.bronze.fetch import BronzeFetcher, FetchResult, FetchStatus
from mdp.bronze.sync import main, plan_backfill_days, plan_files, run_sync
from mdp.runlog.recorder import NullRecorder

TODAY = date(2026, 10, 21)


def _names(files: list[ArchiveFile]) -> list[str]:
    return [file.filename for file in files]


# ---------------------------------------------------------------- 계획


def test_plan_uses_monthly_for_finished_months_and_daily_for_current_month() -> None:
    files = plan_files(Dataset.AGG_TRADES, "BTCUSDT", date(2026, 8, 15), date(2026, 12, 31), TODAY)
    names = _names(files)
    assert names[:2] == ["BTCUSDT-aggTrades-2026-08.zip", "BTCUSDT-aggTrades-2026-09.zip"]
    assert names[2] == "BTCUSDT-aggTrades-2026-10-01.zip"
    assert names[-1] == "BTCUSDT-aggTrades-2026-10-20.zip"  # 오늘(21 일)은 빼고 어제까지
    assert len(files) == 2 + 20


def test_plan_funding_has_no_daily_files() -> None:
    files = plan_files(Dataset.FUNDING_RATE, "BTCUSDT", date(2026, 9, 1), date(2026, 10, 20), TODAY)
    assert _names(files) == ["BTCUSDT-fundingRate-2026-09.zip"]


def test_plan_klines_keeps_interval_and_empty_when_start_is_today() -> None:
    files = plan_files(
        Dataset.KLINES, "BTCUSDT", date(2026, 10, 19), date(2026, 10, 30), TODAY, "1m"
    )
    assert _names(files) == ["BTCUSDT-1m-2026-10-19.zip", "BTCUSDT-1m-2026-10-20.zip"]
    assert plan_files(Dataset.KLINES, "BTCUSDT", TODAY, TODAY, TODAY, "1m") == []


def test_backfill_days_are_daily_sorted_unique_and_not_future() -> None:
    days = [date(2024, 3, 6), date(2024, 3, 5), date(2024, 3, 6)]
    files = plan_backfill_days(Dataset.AGG_TRADES, "BTCUSDT", days, TODAY)
    assert _names(files) == ["BTCUSDT-aggTrades-2024-03-05.zip", "BTCUSDT-aggTrades-2024-03-06.zip"]
    assert all(file.granularity is Granularity.DAILY for file in files)
    with pytest.raises(ValueError, match="오늘 이후"):
        plan_backfill_days(Dataset.AGG_TRADES, "BTCUSDT", [TODAY], TODAY)
    with pytest.raises(ValueError, match="일 파일이 없다"):
        plan_backfill_days(Dataset.FUNDING_RATE, "BTCUSDT", [date(2024, 3, 5)], TODAY)


# ---------------------------------------------------------------- 받기


class FakeSource:
    """경로 → 내용. CHECKSUM 은 내용에서 만들어 준다. 받은 요청을 센다."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[str] = []

    def add(self, file: ArchiveFile, payload: bytes) -> None:
        path = "/" + file.relative_path
        self.files[path] = payload
        digest = hashlib.sha256(payload).hexdigest()
        self.files[path + ".CHECKSUM"] = f"{digest}  {file.filename}\n".encode()

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(f"{request.method} {request.url.path}")
            body = self.files.get(request.url.path)
            if body is None:
                return httpx.Response(404)
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": str(len(body))})
            return httpx.Response(200, content=body)

        return httpx.MockTransport(handler)


def _sync(
    tmp_path: Path,
    source: FakeSource,
    files: list[ArchiveFile],
    *,
    start: date | None = None,
    verify: bool = False,
) -> list[FetchResult]:
    fetcher = BronzeFetcher(
        tmp_path / "bronze", httpx.Client(transport=source.transport()), sleep=lambda _s: None
    )
    return run_sync(
        fetcher,
        files,
        tmp_path / "manifest.csv",
        NullRecorder(),
        {},
        today=TODAY,
        start=start or min(file.period for file in files),
        end=date(2026, 10, 20),
        verify=verify,
    )


def test_second_run_skips_everything_without_asking_the_source(tmp_path: Path) -> None:
    files = plan_files(Dataset.FUNDING_RATE, "BTCUSDT", date(2026, 8, 1), date(2026, 9, 30), TODAY)
    source = FakeSource({})
    for file in files:
        source.add(file, f"rows for {file.stem}\n".encode())

    first = _sync(tmp_path, source, files)
    manifest_after_first = (tmp_path / "manifest.csv").read_bytes()
    calls_after_first = len(source.calls)
    second = _sync(tmp_path, source, files)

    assert [r.status for r in first] == [FetchStatus.DOWNLOADED] * 2
    assert [r.status for r in second] == [FetchStatus.SKIPPED_KNOWN] * 2
    assert len(source.calls) == calls_after_first  # 두 번째는 원천에 묻지 않았다
    assert (tmp_path / "manifest.csv").read_bytes() == manifest_after_first


def test_verify_rechecks_known_files_against_source(tmp_path: Path) -> None:
    files = plan_files(Dataset.FUNDING_RATE, "BTCUSDT", date(2026, 9, 1), date(2026, 9, 30), TODAY)
    source = FakeSource({})
    source.add(files[0], b"rows\n")
    _sync(tmp_path, source, files)
    again = _sync(tmp_path, source, files, verify=True)
    assert [r.status for r in again] == [FetchStatus.ALREADY_PRESENT]


def test_file_changed_on_disk_is_not_trusted(tmp_path: Path) -> None:
    files = plan_files(Dataset.FUNDING_RATE, "BTCUSDT", date(2026, 9, 1), date(2026, 9, 30), TODAY)
    source = FakeSource({})
    source.add(files[0], b"rows\n")
    first = _sync(tmp_path, source, files)
    assert first[0].path is not None
    first[0].path.write_bytes(b"truncated")  # 크기가 달라졌다 → 건너뛰지 않고 대조 → 충돌
    with pytest.raises(Exception, match="원천 체크섬과 다르다"):
        _sync(tmp_path, source, files)


def test_previous_month_without_monthly_file_falls_back_to_daily(tmp_path: Path) -> None:
    files = plan_files(Dataset.AGG_TRADES, "BTCUSDT", date(2026, 9, 29), date(2026, 10, 2), TODAY)
    assert _names(files) == [
        "BTCUSDT-aggTrades-2026-09.zip",
        "BTCUSDT-aggTrades-2026-10-01.zip",
        "BTCUSDT-aggTrades-2026-10-02.zip",
    ]
    source = FakeSource({})
    for day in (29, 30):  # 9 월 월 파일은 아직 없고 일 파일만 있다
        source.add(
            ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2026, 9, day), Granularity.DAILY), b"x"
        )
    source.add(files[1], b"oct1")  # 10-02 는 아직 안 올라옴

    results = _sync(tmp_path, source, files, start=date(2026, 9, 29))
    by_name = {r.file.filename: r.status for r in results}
    assert by_name == {
        "BTCUSDT-aggTrades-2026-09.zip": FetchStatus.MISSING_AT_SOURCE,
        "BTCUSDT-aggTrades-2026-09-29.zip": FetchStatus.DOWNLOADED,
        "BTCUSDT-aggTrades-2026-09-30.zip": FetchStatus.DOWNLOADED,
        "BTCUSDT-aggTrades-2026-10-01.zip": FetchStatus.DOWNLOADED,
        "BTCUSDT-aggTrades-2026-10-02.zip": FetchStatus.MISSING_AT_SOURCE,
    }
    # 대체한 일 파일이 원래 순서 자리(9 월 다음, 10 월 앞)에 들어간다
    assert [r.file.filename for r in results][1:3] == [
        "BTCUSDT-aggTrades-2026-09-29.zip",
        "BTCUSDT-aggTrades-2026-09-30.zip",
    ]


def test_older_missing_month_does_not_fall_back(tmp_path: Path) -> None:
    files = plan_files(Dataset.AGG_TRADES, "BTCUSDT", date(2019, 1, 1), date(2019, 1, 31), TODAY)
    results = _sync(tmp_path, FakeSource({}), files)
    assert [r.status for r in results] == [FetchStatus.MISSING_AT_SOURCE]


def test_main_backfill_days_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mdp.bronze.fetch")
    monkeypatch.delenv("MDP_PG_DSN", raising=False)
    source = FakeSource({})
    day = ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2024, 3, 5), Granularity.DAILY)
    source.add(day, b"trades")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=source.transport(), **kwargs)
    )
    argv = [
        "--dataset",
        "aggTrades",
        "--symbol",
        "BTCUSDT",
        "--days",
        "2024-03-05",
        "--root",
        str(tmp_path / "bronze"),
        "--manifest",
        str(tmp_path / "m.csv"),
    ]
    assert main(argv, today=TODAY) == 0
    assert '"event": "summary"' in caplog.text and '"downloaded": 1' in caplog.text
    assert day.relative_path in (tmp_path / "m.csv").read_text()
