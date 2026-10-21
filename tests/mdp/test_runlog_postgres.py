"""실행 기록 통합 시험 — 진짜 PostgreSQL 에 붙는다.

``docker compose --profile db up -d`` 뒤 ``MDP_TEST_PG_DSN`` 을 주면 돈다(없으면 건너뜀).
시험마다 새 스키마를 만들고 끝나면 지워, 실제 실행 기록 표를 건드리지 않는다.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.conninfo import make_conninfo  # noqa: E402

from mdp.errors import DataIntegrityError  # noqa: E402
from mdp.runlog.migrate import SQL_DIR, apply_migrations  # noqa: E402
from mdp.runlog.recorder import Artifact, PostgresRecorder, record_run  # noqa: E402

pytestmark = pytest.mark.postgres

BASE_DSN = os.environ.get("MDP_TEST_PG_DSN")


@pytest.fixture
def schema_dsn() -> Iterator[str]:
    """시험 전용 스키마를 만들고, 그 스키마만 보는 접속 주소를 준다."""
    if not BASE_DSN:
        pytest.skip("MDP_TEST_PG_DSN 없음")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(BASE_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            yield make_conninfo(BASE_DSN, options=f"-c search_path={schema}")
        finally:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_migrations_apply_once_and_detect_edits(schema_dsn: str, tmp_path: Path) -> None:
    with psycopg.connect(schema_dsn, autocommit=True) as connection:
        assert apply_migrations(connection) == [
            "001_pipeline_runs.sql",
            "002_artifact_table_version.sql",
        ]
        assert apply_migrations(connection) == []

        edited = tmp_path / "sql"
        shutil.copytree(SQL_DIR, edited)
        target = edited / "001_pipeline_runs.sql"
        target.write_text(target.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
        with pytest.raises(DataIntegrityError, match="바뀌었다"):
            apply_migrations(connection, edited)


def test_recorder_writes_run_and_artifacts(schema_dsn: str) -> None:
    with PostgresRecorder(schema_dsn) as recorder:
        with record_run(recorder, "bronze.fetch", {"symbol": "BTCUSDT"}) as run:
            run.add(Artifact("a.zip", "bronze", "downloaded", 10, "a" * 64, elapsed_s=1.5))
            run.add(Artifact("a.zip", "bronze", "already_present", 10, "a" * 64))
        with pytest.raises(RuntimeError), record_run(recorder, "bronze.fetch", {}):
            raise RuntimeError("network down")

    with psycopg.connect(schema_dsn) as connection:
        runs = connection.execute(
            "SELECT status, finished_at IS NOT NULL, params->>'symbol', error, peak_memory_bytes"
            " FROM pipeline_runs ORDER BY started_at"
        ).fetchall()
        artifacts = connection.execute(
            "SELECT artifact_path, status, size_bytes FROM run_artifacts WHERE run_id = %s",
            (run.run_id,),
        ).fetchall()

    assert runs[0][:3] == ("succeeded", True, "BTCUSDT") and runs[0][3] is None
    assert runs[1][0] == "failed" and runs[1][3] == "RuntimeError: network down"
    assert artifacts == [("a.zip", "already_present", 10)]  # 같은 경로는 새 값으로


def test_running_row_cannot_have_finished_at(schema_dsn: str) -> None:
    with (
        PostgresRecorder(schema_dsn),
        psycopg.connect(schema_dsn, autocommit=True) as connection,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        connection.execute(
            "INSERT INTO pipeline_runs VALUES (%s, 'j', 'running', now(), now(), 'c',"
            " '{}', 'h', 'host', NULL, NULL)",
            (uuid.uuid4(),),
        )
