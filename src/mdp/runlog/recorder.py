"""실행 기록 남기기 — 작업 시작 · 산출물 · 끝을 기록하는 창구와 ``with`` 한 줄로 쓰는 도우미.

언제 쓰나: 파이프라인 명령이 ``with record_run(recorder, job, params) as run:`` 으로 감싸고, 파일을
          만들거나 확인할 때마다 ``run.add(Artifact(...))`` 를 부른다.
읽고 쓰는 것: PostgreSQL ``pipeline_runs`` · ``run_artifacts`` (:class:`PostgresRecorder`), git
              커밋(``git rev-parse`` · ``git status``, 읽기만).

[이유] 기록하는 방법(PostgreSQL · 아무것도 안 함 · 시험용 메모리)을 :class:`RunRecorder` 모양 하나로
       맞춰, 파이프라인 코드는 어디에 기록되는지 모른 채 같은 코드로 돈다(의존성 주입).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

__all__ = [
    "Artifact",
    "NullRecorder",
    "PostgresRecorder",
    "RunHandle",
    "RunRecorder",
    "RunStatus",
    "code_version",
    "params_sha256",
    "peak_memory_bytes",
    "record_run",
]

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_ERROR_MAX_CHARS: Final = 2000


class RunStatus(StrEnum):
    """[쓰임] 작업 상태 — 표의 CHECK 제약과 같은 값."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Artifact:
    """[쓰임] 작업이 만든 · 확인한 것 하나 — ``run_artifacts`` 한 줄."""

    path: str
    layer: str
    status: str
    size_bytes: int | None = None
    sha256: str | None = None
    row_count: int | None = None
    elapsed_s: float | None = None


@dataclass(frozen=True, slots=True)
class RunStart:
    """[쓰임] 작업 시작 정보 — ``pipeline_runs`` 의 처음 한 줄."""

    run_id: uuid.UUID
    job: str
    started_at: datetime
    code_version: str
    params: Mapping[str, object]
    params_sha256: str
    host: str


class RunRecorder(Protocol):
    """[쓰임] 기록 창구의 모양 — 시작 · 산출물 더하기 · 끝."""

    def start(self, run: RunStart) -> None: ...

    def add_artifact(self, run_id: uuid.UUID, artifact: Artifact) -> None: ...

    def finish(
        self,
        run_id: uuid.UUID,
        status: RunStatus,
        finished_at: datetime,
        peak_memory: int | None,
        error: str | None,
    ) -> None: ...


class NullRecorder:
    """[쓰임] 아무것도 기록하지 않는 창구 — DB 주소가 없을 때.
    [이유] DB 를 안 띄운 PC(포트폴리오를 보는 사람 등)에서도 파이프라인이 그대로 돌게."""

    def start(self, run: RunStart) -> None:
        """[쓰임] 기록하지 않는다."""

    def add_artifact(self, run_id: uuid.UUID, artifact: Artifact) -> None:
        """[쓰임] 기록하지 않는다."""

    def finish(
        self,
        run_id: uuid.UUID,
        status: RunStatus,
        finished_at: datetime,
        peak_memory: int | None,
        error: str | None,
    ) -> None:
        """[쓰임] 기록하지 않는다."""


class PostgresRecorder:
    """[쓰임] 로컬 PostgreSQL 에 기록하는 창구. 연결할 때 마이그레이션을 적용한다.
    [이유] 부를 때마다 바로 커밋(autocommit)한다 — 작업이 중간에 죽어도 그때까지의 산출물과
           'running' 상태가 남아, 무엇이 끊겼는지 표에서 보인다."""

    def __init__(self, dsn: str) -> None:
        """[쓰임] ``dsn`` 예: ``postgresql://mdp:<비밀번호>@127.0.0.1:56432/mdp``."""
        import psycopg  # DB 를 안 쓰는 실행에서는 psycopg 가 없어도 되게 여기서 import

        from mdp.runlog.migrate import apply_migrations

        self._connection = psycopg.connect(dsn, autocommit=True)
        apply_migrations(self._connection)

    def close(self) -> None:
        """[쓰임] 연결을 닫는다."""
        self._connection.close()

    def __enter__(self) -> PostgresRecorder:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def start(self, run: RunStart) -> None:
        """[쓰임] ``pipeline_runs`` 에 'running' 한 줄을 넣는다."""
        from psycopg.types.json import Jsonb

        self._connection.execute(
            "INSERT INTO pipeline_runs (run_id, job, status, started_at, code_version, params,"
            " params_sha256, host) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run.run_id,
                run.job,
                RunStatus.RUNNING.value,
                run.started_at,
                run.code_version,
                Jsonb(dict(run.params)),
                run.params_sha256,
                run.host,
            ),
        )

    def add_artifact(self, run_id: uuid.UUID, artifact: Artifact) -> None:
        """[쓰임] ``run_artifacts`` 에 한 줄. 같은 작업에서 같은 경로를 다시 넣으면 새 값으로
        바꾼다."""
        self._connection.execute(
            "INSERT INTO run_artifacts (run_id, artifact_path, layer, status, size_bytes, sha256,"
            " row_count, elapsed_s) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (run_id, artifact_path) DO UPDATE SET layer = EXCLUDED.layer,"
            " status = EXCLUDED.status, size_bytes = EXCLUDED.size_bytes,"
            " sha256 = EXCLUDED.sha256, row_count = EXCLUDED.row_count,"
            " elapsed_s = EXCLUDED.elapsed_s",
            (
                run_id,
                artifact.path,
                artifact.layer,
                artifact.status,
                artifact.size_bytes,
                artifact.sha256,
                artifact.row_count,
                artifact.elapsed_s,
            ),
        )

    def finish(
        self,
        run_id: uuid.UUID,
        status: RunStatus,
        finished_at: datetime,
        peak_memory: int | None,
        error: str | None,
    ) -> None:
        """[쓰임] 그 작업 줄의 상태 · 끝난 시각 · 최대 메모리 · 오류를 채운다."""
        self._connection.execute(
            "UPDATE pipeline_runs SET status = %s, finished_at = %s, peak_memory_bytes = %s,"
            " error = %s WHERE run_id = %s",
            (status.value, finished_at, peak_memory, error, run_id),
        )


@dataclass(slots=True)
class RunHandle:
    """[쓰임] ``with record_run(...) as run`` 의 ``run`` — 산출물을 더하는 손잡이."""

    run_id: uuid.UUID
    _recorder: RunRecorder
    artifacts: list[Artifact] = field(default_factory=list)

    def add(self, artifact: Artifact) -> None:
        """[쓰임] 산출물 하나를 바로 기록한다(작업 끝까지 모아 두지 않는다)."""
        self.artifacts.append(artifact)
        self._recorder.add_artifact(self.run_id, artifact)


def code_version(repo_root: Path = REPO_ROOT) -> str:
    """[쓰임] 지금 코드의 git 커밋. 커밋 안 한 변경이 있으면 끝에 ``-dirty``, git 이 없으면
              ``unknown``.
    [이유] '어떤 코드가 이 결과를 만들었나' 를 남겨야 결과가 달라졌을 때 원인을 좁힐 수 있다."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()
        changes = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit}-dirty" if changes else commit


def params_sha256(params: Mapping[str, object]) -> str:
    """[쓰임] 인자를 키 순 정렬 JSON 으로 만든 SHA-256 — 같은 인자면 같은 값.
    [이유] '같은 설정으로 다시 돌린 것인가' 를 표에서 해시 하나로 묶어 보려고."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def peak_memory_bytes() -> int | None:
    """[쓰임] 이 프로세스가 지금까지 쓴 최대 메모리(바이트). 알 수 없으면 None.
    [이유] 벤치마크(4-3)와 '메모리가 6.7 GB 까지 치솟던' 기존 방식 비교에 쓴다. Windows 는 최대 작업
           집합, 리눅스는 ``ru_maxrss``(KiB)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # 핸들은 포인터 크기라 형식을 적어 줘야 한다 — 안 적으면 64 비트에서 32 비트 int 로 잘려
        # '잘못된 핸들'(오류 6)로 실패한다.
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        get_memory_info.restype = wintypes.BOOL
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not get_memory_info(get_current_process(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    else:  # noqa: RET505 — mypy 가 다른 운영체제 쪽을 '닿지 않는 코드' 로 보지 않게 if/else 로 둔다
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


@contextmanager
def record_run(
    recorder: RunRecorder, job: str, params: Mapping[str, object]
) -> Iterator[RunHandle]:
    """[쓰임] ``with record_run(recorder, "bronze.fetch", {...}) as run:`` — 블록이 끝나면 성공,
              예외가 나면 실패(오류 한 줄)로 기록하고 예외는 그대로 다시 던진다.
    [이유] 기록을 빠뜨리거나 실패를 성공으로 남기는 실수를 작업마다 막으려고 한 곳에 모았다."""
    # 1 단계: 시작 기록 — 누가(코드 버전 · 컴퓨터) 무엇을(작업 · 인자) 언제.
    run = RunStart(
        run_id=uuid.uuid4(),
        job=job,
        started_at=datetime.now(UTC),
        code_version=code_version(),
        params=dict(params),
        params_sha256=params_sha256(params),
        host=platform.node() or os.environ.get("COMPUTERNAME", "unknown"),
    )
    recorder.start(run)
    # 2 단계: 작업을 돌리고, 끝나는 방식(정상 · 예외)에 따라 끝 기록.
    try:
        yield RunHandle(run.run_id, recorder)
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"[:_ERROR_MAX_CHARS]
        recorder.finish(run.run_id, RunStatus.FAILED, datetime.now(UTC), peak_memory_bytes(), error)
        raise
    recorder.finish(run.run_id, RunStatus.SUCCEEDED, datetime.now(UTC), peak_memory_bytes(), None)
