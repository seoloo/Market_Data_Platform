"""마이그레이션 러너.

``storage/sql/NNN_name.sql`` 을 번호 순서대로 적용한다.

두 가지를 기록한다:

* ``schema_migrations`` — 적용 이력 + **체크섬**. 이미 적용된 파일이 나중에 수정되면
  기동을 막는다. 조용한 스키마 드리프트가 가장 찾기 어려운 버그가 된다
* ``schema_version`` — 현재 버전 단일 행. exe 와 DB 버전 불일치 감지용 (D10 §4.3)
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from auto_trading.core.errors import AutoTradingError

__all__ = [
    "Migration",
    "MigrationError",
    "apply_migrations",
    "discover_migrations",
    "read_schema_version",
    "require_schema_version",
    "split_statements",
]

SQL_DIR = Path(__file__).resolve().parent / "sql"
_FILENAME_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9_\-]+)\.sql$")


class MigrationError(AutoTradingError):
    """마이그레이션 적용 실패 또는 스키마 불일치."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        """개행 코드 차이를 무시하고 내용만 해시한다 (Windows/Linux 혼용 대비)."""
        normalized = self.sql.replace("\r\n", "\n").strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def discover_migrations(sql_dir: Path | str = SQL_DIR) -> tuple[Migration, ...]:
    """``NNN_name.sql`` 파일을 번호 순으로 수집한다.

    Raises:
        MigrationError: 파일명 규칙 위반, 버전 번호 중복.
    """
    directory = Path(sql_dir)
    if not directory.is_dir():
        raise MigrationError(f"마이그레이션 디렉터리가 없다: {directory}")

    migrations: list[Migration] = []
    seen: dict[int, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"마이그레이션 파일명 규칙 위반: {path.name} (NNN_name.sql 형식이어야 한다)"
            )
        version = int(match.group(1))
        if version in seen:
            raise MigrationError(f"버전 번호 중복: {version} ({seen[version]}, {path.name})")
        seen[version] = path.name
        migrations.append(
            Migration(
                version=version,
                name=match.group(2),
                path=path,
                sql=path.read_text(encoding="utf-8"),
            )
        )
    return tuple(migrations)


def _bootstrap_tables(conn: sqlite3.Connection) -> None:
    """첫 마이그레이션 자체가 이 테이블들을 만들기 전에 필요하므로 미리 만든다.

    ``IF NOT EXISTS`` 로 만들고, 001 의 ``CREATE TABLE`` 과 충돌하지 않도록
    001 적용 전에는 만들지 않는다 — 대신 001 적용 직후 존재를 확인한다.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " checksum TEXT NOT NULL,"
        " applied_ms INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " version INTEGER NOT NULL,"
        " updated_ms INTEGER NOT NULL)"
    )


def split_statements(sql: str) -> list[str]:
    """SQL 스크립트를 개별 구문으로 나눈다.

    ``sqlite3.executescript`` 를 쓰지 않는 이유: 그 함수는 실행 전에 **암묵적으로 커밋**하므로
    우리가 연 트랜잭션이 사라진다. 그러면 마이그레이션이 부분 적용된 채로 남을 수 있다.

    문자열 리터럴(``''`` 이스케이프 포함)과 ``--`` 주석 안의 세미콜론은 구분자로 보지 않는다.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_string = False
    in_comment = False
    i = 0
    length = len(sql)

    while i < length:
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        if in_comment:
            if char == "\n":
                in_comment = False
                buffer.append(char)
            i += 1
            continue

        if in_string:
            buffer.append(char)
            if char == "'":
                if nxt == "'":  # 이스케이프된 따옴표
                    buffer.append(nxt)
                    i += 2
                    continue
                in_string = False
            i += 1
            continue

        if char == "-" and nxt == "-":
            in_comment = True
            i += 2
            continue
        if char == "'":
            in_string = True
            buffer.append(char)
            i += 1
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            i += 1
            continue

        buffer.append(char)
        i += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _applied(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(r["version"]): str(r["checksum"]) for r in rows}


def _verify_checksums(applied: dict[int, str], migrations: tuple[Migration, ...]) -> None:
    """이미 적용된 마이그레이션 파일이 수정되었는지 확인한다.

    수정을 허용하면 "내 DB 스키마가 이 파일과 같다"는 보장이 사라진다.
    """
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            raise MigrationError(
                f"이미 적용된 마이그레이션이 수정되었다: {migration.path.name}\n"
                f"  기록된 체크섬: {recorded[:16]}\n"
                f"  현재 파일    : {migration.checksum[:16]}\n"
                "적용된 마이그레이션은 수정하지 않는다. 새 파일을 추가할 것."
            )


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    sql_dir: Path | str = SQL_DIR,
    now_ms: int | None = None,
) -> tuple[int, ...]:
    """미적용 마이그레이션을 순서대로 적용한다.

    각 마이그레이션은 **하나의 트랜잭션**으로 적용된다. 중간에 실패하면
    그 마이그레이션 전체가 롤백되어 부분 적용 상태가 남지 않는다.

    Returns:
        이번 호출에서 적용한 버전 번호들.
    """
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    migrations = discover_migrations(sql_dir)
    if not migrations:
        raise MigrationError(f"적용할 마이그레이션이 없다: {sql_dir}")

    _bootstrap_tables(conn)
    applied = _applied(conn)
    _verify_checksums(applied, migrations)

    newly_applied: list[int] = []
    for migration in migrations:
        if migration.version in applied:
            continue
        statements = split_statements(migration.sql)
        if any(s.upper().startswith("CREATE TRIGGER") for s in statements):
            raise MigrationError(
                f"{migration.path.name}: CREATE TRIGGER 는 지원하지 않는다. "
                "구문 분할기가 BEGIN...END 블록을 처리하지 못한다."
            )

        conn.execute("BEGIN")
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_ms) "
                "VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, timestamp),
            )
            conn.execute(
                "INSERT INTO schema_version (id, version, updated_ms) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version, "
                "updated_ms = excluded.updated_ms",
                (migration.version, timestamp),
            )
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"마이그레이션 실패: {migration.path.name}\n{exc}") from exc
        else:
            conn.execute("COMMIT")
            newly_applied.append(migration.version)

    return tuple(newly_applied)


def read_schema_version(conn: sqlite3.Connection) -> int:
    """현재 스키마 버전. 미적용이면 0."""
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    except sqlite3.Error:
        return 0
    return 0 if row is None else int(row["version"])


def require_schema_version(conn: sqlite3.Connection, expected: int) -> None:
    """스키마 버전이 기대값과 다르면 즉시 실패시킨다 (D10 §4.3).

    exe 가 DB 보다 오래된 경우 **조용히 잘못 읽는 것이 최악**이다.
    대시보드와 매매 프로세스 모두 기동 시 이 검사를 통과해야 한다.
    """
    actual = read_schema_version(conn)
    if actual == expected:
        return
    if actual == 0:
        raise MigrationError(
            "DB 스키마가 초기화되지 않았다. 매매 프로세스를 먼저 실행해 마이그레이션을 적용할 것."
        )
    hint = (
        "프로그램이 DB 보다 오래되었다. 최신 버전을 내려받을 것."
        if actual > expected
        else "DB 가 프로그램보다 오래되었다. 마이그레이션을 적용할 것."
    )
    raise MigrationError(f"스키마 버전 불일치: DB v{actual} / 프로그램 v{expected}. {hint}")


def latest_version(sql_dir: Path | str = SQL_DIR) -> int:
    """소스에 존재하는 마이그레이션 중 가장 높은 버전."""
    migrations = discover_migrations(sql_dir)
    return migrations[-1].version if migrations else 0
