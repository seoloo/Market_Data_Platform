"""마이그레이션 러너 — ``storage/sql/NNN_name.sql`` 을 번호 순서대로 한 번씩만 적용한다.

언제 쓰나: DB 를 여는 실행기 · 연구 스크립트가 시작할 때 :func:`apply_migrations` 를 부른다.
읽고 쓰는 것: ``storage/sql/*.sql`` (읽기만) → DB 의 ``schema_migrations`` · ``schema_version`` 표와
              SQL 파일이 만드는 표들.

두 가지를 기록한다:

* ``schema_migrations`` — 적용 이력 + **체크섬**. 이미 적용된 파일이 나중에 수정되면
  기동을 막는다. 조용한 스키마 드리프트가 가장 찾기 어려운 버그가 된다
* ``schema_version`` — 현재 버전 단일 행. exe 와 DB 버전 불일치 감지용 (D10 §4.3)

⚠️ 이미 적용된 ``.sql`` 파일은 글자 하나도 고치지 않는다 — 체크섬이 바뀌어 기존 DB 가 열리지 않는다.
바꿀 것이 있으면 새 번호 파일을 더한다.
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
"""마이그레이션 SQL 파일이 있는 폴더 (``storage/sql``)."""

_FILENAME_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9_\-]+)\.sql$")
"""파일 이름 규칙 ``NNN_name.sql`` — 앞 숫자가 버전, 뒤가 이름."""


class MigrationError(AutoTradingError):
    """마이그레이션 적용 실패 또는 스키마 불일치."""


@dataclass(frozen=True, slots=True)
class Migration:
    """SQL 파일 하나 = 마이그레이션 하나 (버전 · 이름 · 경로 · 내용)."""

    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        """[쓰임] 파일 내용의 SHA-256 — ``schema_migrations`` 에 적어 두고 다음 기동 때 비교한다.
        [이유] 개행 코드(CRLF/LF) 차이와 앞뒤 공백은 무시한다 — Windows 와 Linux 에서 같은 파일이
               다른 체크섬이 되어 기동이 막히는 일을 피한다."""
        normalized = self.sql.replace("\r\n", "\n").strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def discover_migrations(sql_dir: Path | str = SQL_DIR) -> tuple[Migration, ...]:
    """[쓰임] 폴더의 ``NNN_name.sql`` 파일을 모두 읽어 번호 순으로 돌려준다.
    [이유] 이름 규칙을 어긴 파일이나 같은 번호가 둘 있으면 바로 :class:`MigrationError` — 어느
           파일이 먼저 적용될지 애매한 상태로 DB 를 바꾸지 않는다."""
    directory = Path(sql_dir)
    if not directory.is_dir():
        raise MigrationError(f"마이그레이션 디렉터리가 없다: {directory}")

    migrations: list[Migration] = []
    filename_by_version: dict[int, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"마이그레이션 파일명 규칙 위반: {path.name} (NNN_name.sql 형식이어야 한다)"
            )
        version = int(match.group(1))
        if version in filename_by_version:
            raise MigrationError(
                f"버전 번호 중복: {version} ({filename_by_version[version]}, {path.name})"
            )
        filename_by_version[version] = path.name
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
    """[쓰임] 기록용 표 ``schema_migrations`` · ``schema_version`` 이 없으면 만든다.
    [이유] 첫 마이그레이션을 적용한 사실도 어딘가에 적어야 하므로, 001 보다 먼저 있어야 한다.
           ``IF NOT EXISTS`` 라 여러 번 불러도 안전하다."""
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
    """[쓰임] SQL 스크립트를 세미콜론(``;``) 기준으로 구문 하나씩 나눈다.
    [이유] ``sqlite3.executescript`` 는 실행 전에 **암묵적으로 커밋**해서 우리가 연 트랜잭션이
           사라지고, 마이그레이션이 반쯤 적용된 채 남을 수 있다. 그래서 직접 나눠 한 트랜잭션 안에서
           하나씩 실행한다. 문자열(``'...'``, ``''`` 이스케이프 포함)과 ``--`` 주석 안의 세미콜론은
           구분자로 보지 않는다."""
    statements: list[str] = []
    current_chars: list[str] = []
    in_string = False
    in_comment = False
    position = 0
    length = len(sql)

    while position < length:
        char = sql[position]
        next_char = sql[position + 1] if position + 1 < length else ""

        # 1 단계: 주석 안 — 줄 끝까지 버린다 (줄바꿈은 남긴다)
        if in_comment:
            if char == "\n":
                in_comment = False
                current_chars.append(char)
            position += 1
            continue

        # 2 단계: 문자열 안 — 닫는 따옴표가 나올 때까지 그대로 담는다
        if in_string:
            current_chars.append(char)
            if char == "'":
                if next_char == "'":  # 이스케이프된 따옴표
                    current_chars.append(next_char)
                    position += 2
                    continue
                in_string = False
            position += 1
            continue

        # 3 단계: 바깥 — 주석 시작 · 문자열 시작 · 구문 끝을 찾는다
        if char == "-" and next_char == "-":
            in_comment = True
            position += 2
            continue
        if char == "'":
            in_string = True
            current_chars.append(char)
            position += 1
            continue
        if char == ";":
            statement = "".join(current_chars).strip()
            if statement:
                statements.append(statement)
            current_chars = []
            position += 1
            continue

        current_chars.append(char)
        position += 1

    trailing = "".join(current_chars).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _load_applied_checksums(conn: sqlite3.Connection) -> dict[int, str]:
    """[쓰임] 이미 적용된 마이그레이션의 버전 → 체크섬 (``schema_migrations`` 에서 읽음)."""
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(row["version"]): str(row["checksum"]) for row in rows}


def _verify_checksums(applied: dict[int, str], migrations: tuple[Migration, ...]) -> None:
    """[쓰임] 이미 적용된 마이그레이션 파일이 그 뒤에 수정됐는지 확인한다 — 바뀌었으면 오류.
    [이유] 수정을 허용하면 "내 DB 스키마가 이 파일과 같다" 는 보장이 사라진다."""
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
    """[쓰임] 남은 마이그레이션을 번호 순서대로 적용하고, 이번에 적용한 버전들을 돌려준다.
    [이유] 각 마이그레이션은 **하나의 트랜잭션**이다 — 중간에 실패하면 그 파일 전체가 롤백되어 반쯤
           적용된 상태가 남지 않는다. ``now_ms`` 는 시험에서 기록 시각을 고정할 때 쓴다."""
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    migrations = discover_migrations(sql_dir)
    if not migrations:
        raise MigrationError(f"적용할 마이그레이션이 없다: {sql_dir}")

    # 1 단계: 기록 표 준비 · 이미 적용된 파일이 바뀌지 않았는지 확인
    _bootstrap_tables(conn)
    applied = _load_applied_checksums(conn)
    _verify_checksums(applied, migrations)

    # 2 단계: 남은 파일을 하나씩 트랜잭션으로 적용 + 기록
    newly_applied: list[int] = []
    for migration in migrations:
        if migration.version in applied:
            continue
        statements = split_statements(migration.sql)
        if any(statement.upper().startswith("CREATE TRIGGER") for statement in statements):
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
    """[쓰임] DB 의 현재 스키마 버전. 아직 아무것도 적용하지 않았으면(표가 없으면) 0."""
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    except sqlite3.Error:
        return 0
    return 0 if row is None else int(row["version"])


def require_schema_version(conn: sqlite3.Connection, expected: int) -> None:
    """[쓰임] DB 스키마 버전이 ``expected`` 와 다르면 이유를 담아 바로 실패시킨다 (D10 §4.3).
    [이유] 프로그램이 DB 보다 오래된 경우 **조용히 잘못 읽는 것이 최악**이다. 대시보드와 매매
           프로세스 모두 기동 시 이 검사를 통과해야 한다."""
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
    """[쓰임] 소스 폴더에 있는 마이그레이션 중 가장 높은 버전 (없으면 0)."""
    migrations = discover_migrations(sql_dir)
    return migrations[-1].version if migrations else 0
