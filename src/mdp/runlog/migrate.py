"""실행 기록 DB 마이그레이션 — ``sql/NNN_*.sql`` 을 번호 순으로 한 번씩 적용한다.

언제 쓰나: :class:`mdp.runlog.recorder.PostgresRecorder` 가 연결할 때마다 부른다(이미 적용된 것은
           건너뜀).
읽고 쓰는 것: ``mdp/runlog/sql/*.sql`` (읽기) → PostgreSQL ``schema_migrations`` 표 · 그 파일이
              만드는 표.

[이유] 이미 적용된 파일을 고치면 DB 마다 표 모양이 달라진다. 그래서 적용한 파일의 체크섬을 남기고,
       나중에 같은 번호 파일의 체크섬이 다르면 멈춘다(원본 저장소 ``storage/migrations.py`` 와 같은
       약속).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import psycopg
from psycopg.rows import TupleRow

from mdp.errors import DataIntegrityError

__all__ = ["Migration", "apply_migrations", "load_migrations"]

SQL_DIR: Final = Path(__file__).resolve().parent / "sql"
_FILENAME: Final = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    """[쓰임] SQL 파일 하나 — 번호 · 파일 이름 · 내용 · 내용의 SHA-256."""

    version: int
    filename: str
    sql: str
    sha256: str


def load_migrations(sql_dir: Path = SQL_DIR) -> list[Migration]:
    """[쓰임] 폴더의 ``NNN_이름.sql`` 을 번호 순으로 읽는다.
    [이유] 번호가 겹치거나 이름 규칙이 틀린 파일은 실수일 가능성이 커서 바로 멈춘다."""
    migrations: list[Migration] = []
    filename_by_version: dict[int, str] = {}
    for path in sorted(sql_dir.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise ValueError(f"마이그레이션 파일 이름 규칙(NNN_이름.sql)과 다르다: {path.name}")
        version = int(match.group(1))
        if version in filename_by_version:
            raise ValueError(f"번호가 겹친다: {filename_by_version[version]} · {path.name}")
        filename_by_version[version] = path.name
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(version, path.name, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest())
        )
    return migrations


def apply_migrations(
    connection: psycopg.Connection[TupleRow], sql_dir: Path = SQL_DIR
) -> list[str]:
    """[쓰임] 아직 적용 안 된 마이그레이션을 번호 순으로 적용하고, 적용한 파일 이름 목록을 돌려준다.
    [이유] 파일 하나 = 트랜잭션 하나 — 중간에 실패하면 그 파일은 통째로 되돌아가 반쯤 만든 표가 남지
           않는다. 이미 적용된 파일의 체크섬이 다르면 :class:`DataIntegrityError`."""
    # 1 단계: 기록 표를 만들고 이미 적용된 것을 읽는다.
    with connection.transaction():
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version integer PRIMARY KEY, filename text NOT NULL,"
            " sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        rows = connection.execute("SELECT version, sha256 FROM schema_migrations").fetchall()
    applied_sha256_by_version = {int(version): str(sha) for version, sha in rows}

    # 2 단계: 적용된 것은 체크섬만 대조하고, 새 것은 하나씩 적용한다.
    applied_now: list[str] = []
    for migration in load_migrations(sql_dir):
        recorded_sha256 = applied_sha256_by_version.get(migration.version)
        if recorded_sha256 is not None:
            if recorded_sha256 != migration.sha256:
                raise DataIntegrityError(
                    f"이미 적용된 마이그레이션이 바뀌었다: {migration.filename} "
                    "— 고치지 말고 새 번호로"
                )
            continue
        with connection.transaction():
            connection.execute(migration.sql.encode("utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations (version, filename, sha256) VALUES (%s, %s, %s)",
                (migration.version, migration.filename, migration.sha256),
            )
        applied_now.append(migration.filename)
    return applied_now
