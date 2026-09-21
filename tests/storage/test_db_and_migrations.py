"""DB 연결 및 마이그레이션 테스트.

특히 **읽기 전용 보장**(D10 §7)과 **체크섬 드리프트 감지**를 집중 검증한다.
둘 다 조용히 잘못되면 찾기 어려운 종류의 문제다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import ClassVar

import pytest

from auto_trading.storage.db import Database, DatabaseError, connect, connect_read_only
from auto_trading.storage.migrations import (
    MigrationError,
    apply_migrations,
    discover_migrations,
    latest_version,
    read_schema_version,
    require_schema_version,
)

NOW = 1_726_000_000_000


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def migrated(db_path: Path):
    conn = connect(db_path)
    apply_migrations(conn, now_ms=NOW)
    yield conn
    conn.close()


class TestConnect:
    def test_creates_parent_directory(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "test.db"
        conn = connect(nested)
        assert nested.is_file()
        conn.close()

    def test_wal_mode_enabled(self, db_path: Path):
        conn = connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        conn.close()

    def test_foreign_keys_enabled(self, db_path: Path):
        conn = connect(db_path)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()

    def test_row_factory_gives_named_access(self, migrated: sqlite3.Connection):
        row = migrated.execute("SELECT 1 AS answer").fetchone()
        assert row["answer"] == 1


class TestReadOnlyConnection:
    """D10 협상 불가 제약: 대시보드는 쓰기가 물리적으로 불가능해야 한다."""

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(DatabaseError, match="읽기 전용으로 열 DB 가 없다"):
            connect_read_only(tmp_path / "nope.db")

    def test_insert_is_rejected(self, db_path: Path, migrated: sqlite3.Connection):
        ro = connect_read_only(db_path)
        with pytest.raises(sqlite3.OperationalError):
            ro.execute(
                "INSERT INTO config_versions (hash, config_json, created_ms) VALUES ('x', '{}', 1)"
            )
        ro.close()

    def test_update_is_rejected(self, db_path: Path, migrated: sqlite3.Connection):
        ro = connect_read_only(db_path)
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("UPDATE schema_version SET version = 99")
        ro.close()

    def test_create_table_is_rejected(self, db_path: Path, migrated: sqlite3.Connection):
        ro = connect_read_only(db_path)
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("CREATE TABLE evil (x INTEGER)")
        ro.close()

    def test_select_works(self, db_path: Path, migrated: sqlite3.Connection):
        ro = connect_read_only(db_path)
        assert read_schema_version(ro) == latest_version()
        ro.close()

    def test_query_only_pragma_is_set(self, db_path: Path, migrated: sqlite3.Connection):
        ro = connect_read_only(db_path)
        assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
        ro.close()

    def test_concurrent_read_while_writer_open(self, db_path: Path, migrated: sqlite3.Connection):
        """WAL 이므로 쓰기 연결이 열려 있어도 읽기가 가능해야 한다."""
        migrated.execute(
            "INSERT INTO config_versions (hash, config_json, created_ms) VALUES ('h', '{}', 1)"
        )
        ro = connect_read_only(db_path)
        assert ro.execute("SELECT COUNT(*) AS c FROM config_versions").fetchone()["c"] == 1
        ro.close()


class TestDatabaseWrapper:
    def test_transaction_commits(self, db_path: Path):
        with Database(db_path) as db:
            apply_migrations(db.connection, now_ms=NOW)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO config_versions (hash, config_json, created_ms)"
                    " VALUES ('a', '{}', 1)"
                )
            assert (
                db.connection.execute("SELECT COUNT(*) AS c FROM config_versions").fetchone()["c"]
                == 1
            )

    def test_transaction_rolls_back_on_error(self, db_path: Path):
        with Database(db_path) as db:
            apply_migrations(db.connection, now_ms=NOW)
            with pytest.raises(RuntimeError), db.transaction() as conn:
                conn.execute(
                    "INSERT INTO config_versions (hash, config_json, created_ms)"
                    " VALUES ('a', '{}', 1)"
                )
                raise RuntimeError("의도적 실패")
            assert (
                db.connection.execute("SELECT COUNT(*) AS c FROM config_versions").fetchone()["c"]
                == 0
            )

    def test_read_only_forbids_transaction(self, db_path: Path):
        with Database(db_path) as db:
            apply_migrations(db.connection, now_ms=NOW)
        with (
            Database(db_path, read_only=True) as ro,
            pytest.raises(DatabaseError, match="읽기 전용"),
            ro.transaction(),
        ):
            pass


class TestDiscoverMigrations:
    def test_finds_initial(self):
        migrations = discover_migrations()
        assert len(migrations) >= 1
        assert migrations[0].version == 1
        assert migrations[0].name == "initial"

    def test_versions_are_sorted(self):
        versions = [m.version for m in discover_migrations()]
        assert versions == sorted(versions)

    def test_bad_filename_rejected(self, tmp_path: Path):
        (tmp_path / "oops.sql").write_text("SELECT 1;", encoding="utf-8")
        with pytest.raises(MigrationError, match="파일명 규칙"):
            discover_migrations(tmp_path)

    def test_duplicate_version_rejected(self, tmp_path: Path):
        (tmp_path / "001_a.sql").write_text("SELECT 1;", encoding="utf-8")
        (tmp_path / "001_b.sql").write_text("SELECT 1;", encoding="utf-8")
        with pytest.raises(MigrationError, match="버전 번호 중복"):
            discover_migrations(tmp_path)

    def test_missing_directory_rejected(self, tmp_path: Path):
        with pytest.raises(MigrationError, match="디렉터리가 없다"):
            discover_migrations(tmp_path / "nope")

    def test_checksum_ignores_line_endings(self, tmp_path: Path):
        """Windows 개발 / Linux 운영 혼용에서 CRLF 차이로 오탐이 나면 안 된다."""
        (tmp_path / "001_a.sql").write_text("SELECT 1;\nSELECT 2;\n", encoding="utf-8")
        unix = discover_migrations(tmp_path)[0].checksum
        (tmp_path / "001_a.sql").write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
        assert discover_migrations(tmp_path)[0].checksum == unix


class TestApplyMigrations:
    def test_applies_and_records(self, db_path: Path):
        conn = connect(db_path)
        applied = apply_migrations(conn, now_ms=NOW)
        # 마이그레이션이 늘어나도 깨지지 않게, 번호 자체가 아니라 순서·기록을 본다.
        assert applied == tuple(m.version for m in discover_migrations())
        assert read_schema_version(conn) == max(applied)
        row = conn.execute("SELECT * FROM schema_migrations WHERE version = 1").fetchone()
        assert row["name"] == "initial"
        assert row["applied_ms"] == NOW
        conn.close()

    def test_is_idempotent(self, db_path: Path):
        conn = connect(db_path)
        apply_migrations(conn, now_ms=NOW)
        assert apply_migrations(conn, now_ms=NOW) == ()
        conn.close()

    def test_version_before_migration_is_zero(self, db_path: Path):
        conn = connect(db_path)
        assert read_schema_version(conn) == 0
        conn.close()

    def test_modified_migration_is_detected(self, tmp_path: Path, db_path: Path):
        """적용된 마이그레이션을 수정하면 조용한 스키마 드리프트가 생긴다."""
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        (sql_dir / "001_x.sql").write_text("CREATE TABLE t (a INTEGER);", encoding="utf-8")
        conn = connect(db_path)
        apply_migrations(conn, sql_dir=sql_dir, now_ms=NOW)

        (sql_dir / "001_x.sql").write_text("CREATE TABLE t (a TEXT);", encoding="utf-8")
        with pytest.raises(MigrationError, match="수정되었다"):
            apply_migrations(conn, sql_dir=sql_dir, now_ms=NOW)
        conn.close()

    def test_failed_migration_rolls_back(self, tmp_path: Path, db_path: Path):
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        (sql_dir / "001_bad.sql").write_text(
            "CREATE TABLE ok (a INTEGER);\nTHIS IS NOT SQL;", encoding="utf-8"
        )
        conn = connect(db_path)
        with pytest.raises(MigrationError, match="마이그레이션 실패"):
            apply_migrations(conn, sql_dir=sql_dir, now_ms=NOW)
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "ok" not in tables  # 부분 적용이 남지 않는다
        assert read_schema_version(conn) == 0
        conn.close()

    def test_empty_directory_rejected(self, tmp_path: Path, db_path: Path):
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        conn = connect(db_path)
        with pytest.raises(MigrationError, match="적용할 마이그레이션이 없다"):
            apply_migrations(conn, sql_dir=sql_dir)
        conn.close()

    def test_applies_in_order(self, tmp_path: Path, db_path: Path):
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        (sql_dir / "001_a.sql").write_text("CREATE TABLE t (a INTEGER);", encoding="utf-8")
        (sql_dir / "002_b.sql").write_text("ALTER TABLE t ADD COLUMN b TEXT;", encoding="utf-8")
        conn = connect(db_path)
        assert apply_migrations(conn, sql_dir=sql_dir, now_ms=NOW) == (1, 2)
        assert read_schema_version(conn) == 2
        conn.close()


class TestRequireSchemaVersion:
    """D10 §4.3 — 조용히 잘못 읽는 것이 최악이다."""

    def test_match_passes(self, migrated: sqlite3.Connection):
        require_schema_version(migrated, latest_version())

    def test_uninitialized_db_message(self, db_path: Path):
        conn = connect(db_path)
        with pytest.raises(MigrationError, match="초기화되지 않았다"):
            require_schema_version(conn, 1)
        conn.close()

    def test_program_older_than_db(self, migrated: sqlite3.Connection):
        with pytest.raises(MigrationError, match="프로그램이 DB 보다 오래되었다"):
            require_schema_version(migrated, 0 if latest_version() > 1 else -1)

    def test_db_older_than_program(self, migrated: sqlite3.Connection):
        with pytest.raises(MigrationError, match="DB 가 프로그램보다 오래되었다"):
            require_schema_version(migrated, latest_version() + 1)


class TestInitialSchema:
    """03-data-model.md 의 테이블·뷰가 실제로 생성되는지 확인한다."""

    EXPECTED_TABLES: ClassVar[set[str]] = {
        "config_versions",
        "code_versions",
        "instruments",
        "candles",
        "candle_gaps",
        "funding_rates",
        "fee_schedule",
        "decisions",
        "positions",
        "intents",
        "orders",
        "fills",
        "trades",
        "account_snapshots",
        "risk_state",
        "risk_events",
        "macro_events",
        "news_items",
        "news_analyses",
        "system_events",
        "errors",
        "heartbeats",
        "backtest_runs",
        "schema_version",
        "schema_migrations",
    }

    EXPECTED_VIEWS: ClassVar[set[str]] = {"v_daily_pnl", "v_daily_equity", "v_trade_explain"}

    def test_all_tables_created(self, migrated: sqlite3.Connection):
        actual = {
            r["name"]
            for r in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert actual >= self.EXPECTED_TABLES

    def test_all_views_created(self, migrated: sqlite3.Connection):
        actual = {
            r["name"]
            for r in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
        }
        assert actual == self.EXPECTED_VIEWS

    @pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
    def test_views_are_queryable(self, migrated: sqlite3.Connection, view: str):
        """빈 DB 에서도 뷰가 오류 없이 조회되어야 한다."""
        migrated.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()

    def test_foreign_key_is_enforced(self, migrated: sqlite3.Connection):
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute(
                "INSERT INTO fills (order_id, exchange_trade_id, ts_ms, qty, price, fee,"
                " fee_asset, is_maker) VALUES (999, 't1', 1, '1', '1', '0', 'USDT', 0)"
            )

    def test_check_constraint_rejects_bad_enum(self, migrated: sqlite3.Connection):
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute(
                "INSERT INTO heartbeats (component, ts_ms, state) VALUES ('main', 1, 'NOT_A_STATE')"
            )

    def test_schema_version_single_row_constraint(self, migrated: sqlite3.Connection):
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute("INSERT INTO schema_version (id, version, updated_ms) VALUES (2,1,1)")
