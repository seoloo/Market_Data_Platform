"""실행 기록 시험 — DB 없이 메모리 기록 창구로 시작 · 산출물 · 끝 기록과 실패 처리를 본다."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from mdp.bronze.archive import ArchiveFile, Dataset
from mdp.bronze.fetch import BronzeFetcher, run_fetch
from mdp.runlog.migrate import load_migrations
from mdp.runlog.recorder import (
    Artifact,
    RunStart,
    RunStatus,
    code_version,
    params_sha256,
    peak_memory_bytes,
    record_run,
)


class MemoryRecorder:
    """시험용 기록 창구 — 부른 순서대로 사건을 모은다."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.finishes: list[tuple[RunStatus, int | None, str | None]] = []

    def start(self, run: RunStart) -> None:
        self.events.append(("start", run))

    def add_artifact(self, run_id: uuid.UUID, artifact: Artifact) -> None:
        self.events.append(("artifact", artifact))

    def finish(
        self,
        run_id: uuid.UUID,
        status: RunStatus,
        finished_at: datetime,
        peak_memory: int | None,
        error: str | None,
    ) -> None:
        self.events.append(("finish", status))
        self.finishes.append((status, peak_memory, error))


def test_record_run_success_writes_start_artifacts_finish() -> None:
    recorder = MemoryRecorder()
    with record_run(recorder, "bronze.fetch", {"symbol": "BTCUSDT"}) as run:
        run.add(Artifact("a.zip", "bronze", "downloaded", 10, "f" * 64))

    kinds = [kind for kind, _ in recorder.events]
    assert kinds == ["start", "artifact", "finish"]
    start = recorder.events[0][1]
    assert isinstance(start, RunStart) and start.job == "bronze.fetch"
    assert start.run_id == run.run_id and start.started_at.tzinfo is not None
    status, peak_memory, error = recorder.finishes[-1]
    assert status is RunStatus.SUCCEEDED and error is None
    assert peak_memory is not None and peak_memory > 0


def test_record_run_failure_is_recorded_and_reraised() -> None:
    recorder = MemoryRecorder()
    with pytest.raises(RuntimeError, match="boom"), record_run(recorder, "job", {}):
        raise RuntimeError("boom")
    status, _, error = recorder.finishes[-1]
    assert status is RunStatus.FAILED and error == "RuntimeError: boom"


def test_params_hash_ignores_key_order() -> None:
    assert params_sha256({"a": 1, "b": "x"}) == params_sha256({"b": "x", "a": 1})
    assert params_sha256({"a": 1}) != params_sha256({"a": 2})


def test_code_version_is_commit_or_unknown(tmp_path: Path) -> None:
    assert re.fullmatch(r"[0-9a-f]{40}(-dirty)?", code_version())
    assert code_version(tmp_path) == "unknown"


def test_peak_memory_is_measured_on_windows_and_posix() -> None:
    # Windows 와 리눅스 모두 잴 수 있어야 한다 — None 이면 측정 코드가 조용히 실패한 것
    measured = peak_memory_bytes()
    assert measured is not None and measured > 1_000_000


def test_migration_files_follow_rules(tmp_path: Path) -> None:
    migrations = load_migrations()
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)
    assert migrations[0].filename == "001_pipeline_runs.sql"

    (tmp_path / "001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_b.sql").write_text("SELECT 2;", encoding="utf-8")
    with pytest.raises(ValueError, match="겹친다"):
        load_migrations(tmp_path)
    (tmp_path / "001_b.sql").unlink()
    (tmp_path / "2_bad.sql").write_text("SELECT 3;", encoding="utf-8")
    with pytest.raises(ValueError, match="이름 규칙"):
        load_migrations(tmp_path)


def test_run_fetch_keeps_manifest_and_marks_failure_when_second_file_breaks(
    tmp_path: Path,
) -> None:
    good = ArchiveFile(Dataset.FUNDING_RATE, "BTCUSDT", date(2024, 3, 1))
    broken = ArchiveFile(Dataset.FUNDING_RATE, "BTCUSDT", date(2024, 4, 1))
    payload = b"calc_time,funding_interval_hours,last_funding_rate\n"
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    files = {
        "/" + good.relative_path: payload,
        "/" + good.relative_path + ".CHECKSUM": f"{digest}  x.zip".encode(),
        "/" + broken.relative_path: payload + b"corrupt",
        "/" + broken.relative_path + ".CHECKSUM": f"{digest}  y.zip".encode(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = files.get(request.url.path)
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)

    recorder = MemoryRecorder()
    manifest = tmp_path / "manifest.csv"
    fetcher = BronzeFetcher(
        tmp_path / "bronze",
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )
    with pytest.raises(Exception, match="체크섬 불일치"):
        run_fetch(fetcher, [good, broken], manifest, recorder, {"symbol": "BTCUSDT"})

    assert good.relative_path in manifest.read_text()
    assert broken.relative_path not in manifest.read_text()
    kinds = [kind for kind, _ in recorder.events]
    assert kinds == ["start", "artifact", "finish"]
    status, _, error = recorder.finishes[-1]
    assert status is RunStatus.FAILED and error is not None and "DataIntegrityError" in error
