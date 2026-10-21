"""저장 계층 — SQLite 연결, 마이그레이션, repository.

언제 쓰나: DB 를 여는 곳에서 ``from auto_trading.storage import Database`` 처럼 짧게 부를 때.
읽고 쓰는 것: 이 파일은 이름만 모아 둔다 (실제 일은 ``db.py`` · ``migrations.py`` ·
              ``repositories/``).

Pure Core 는 이 패키지를 import 하지 않는다 (AGENTS.md §3.1).
"""

from auto_trading.storage.db import Database, DatabaseError, connect, connect_read_only
from auto_trading.storage.migrations import (
    MigrationError,
    apply_migrations,
    latest_version,
    read_schema_version,
    require_schema_version,
)

__all__ = [
    "Database",
    "DatabaseError",
    "MigrationError",
    "apply_migrations",
    "connect",
    "connect_read_only",
    "latest_version",
    "read_schema_version",
    "require_schema_version",
]
