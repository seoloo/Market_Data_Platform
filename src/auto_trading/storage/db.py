"""SQLite 연결 관리 — 쓰기 연결 · 읽기 전용 연결 · 명시적 트랜잭션을 한 곳에서 만든다.

언제 쓰나: 백필 · 무결성 검사 · Paper 기록 · 대시보드 등 ``trading.db`` 를 여는 모든 곳.
읽고 쓰는 것: 부르는 쪽이 넘긴 SQLite 파일 하나 (WAL 모드라 옆에 ``-wal`` · ``-shm`` 파일이 생긴다).

설계: 03-data-model.md §1, 14-ui-and-packaging.md §2.2

핵심:
    * **WAL 모드** — 쓰기 1개 + 읽기 N개 동시 접근. 대시보드가 매매를 막지 않는다
    * **읽기 전용 연결** — 대시보드용. ``mode=ro`` + ``query_only=ON`` 이중 방어
    * 금액·수량은 TEXT 로 저장한다. REAL 금지 (09-failure-modes.md F10)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

from auto_trading.core.errors import AutoTradingError

__all__ = ["Database", "connect", "connect_read_only"]

_DEFAULT_BUSY_TIMEOUT_MS = 5000
"""다른 연결이 쓰는 중일 때 기다리는 최대 시간(5 초). 넘기면 'database is locked' 오류."""


class DatabaseError(AutoTradingError):
    """DB 연결·설정 오류."""


def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool, busy_timeout_ms: int) -> None:
    """[쓰임] 새 연결에 SQLite 설정(PRAGMA)을 건다 — 기다리는 시간 · 외래 키 · 읽기 전용 또는 WAL.
    [이유] 연결마다 설정이 다르면 어떤 곳은 외래 키 검사를 안 하는 식으로 조용히 어긋난다. 두 연결
           함수가 같은 함수를 거치게 해 설정을 한 곳에서 관리한다."""
    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    conn.execute("PRAGMA foreign_keys = ON")
    if read_only:
        # mode=ro 만으로도 쓰기가 막히지만, 이중으로 건다.
        # UI 가 실수로라도 쓰기를 시도하지 못하게 하는 것이 D10 의 협상 불가 제약이다.
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")


def connect(
    path: Path | str,
    *,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
    create_parents: bool = True,
) -> sqlite3.Connection:
    """[쓰임] 쓰기 가능한 연결을 만든다. 파일이 없으면 새로 만들고, 폴더도 만든다.
    [이유] ``isolation_level=None`` 으로 파이썬의 자동 트랜잭션을 끈다 — 언제 커밋되는지 코드에
           보이는 곳(:meth:`Database.transaction`)에서만 정하기 위해서다. ``row_factory`` 를
           ``sqlite3.Row`` 로 두어 ``row["열 이름"]`` 으로 읽는다."""
    db_path = Path(path)
    if create_parents:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, read_only=False, busy_timeout_ms=busy_timeout_ms)
    return conn


def connect_read_only(
    path: Path | str,
    *,
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """[쓰임] 읽기 전용 연결을 만든다 (대시보드 · 점검용). 쓰려고 하면 ``sqlite3.OperationalError``.
    [이유] 화면 쪽 코드가 실수로라도 장부를 바꾸지 못하게 하는 구조적 보장이다 (D10 §7, 시험으로
           고정). 읽기 전용은 DB 를 새로 만들지 않는다 — 파일이 없으면 :class:`DatabaseError`."""
    db_path = Path(path)
    if not db_path.is_file():
        raise DatabaseError(
            f"읽기 전용으로 열 DB 가 없다: {db_path}. 매매 프로세스를 먼저 실행해야 한다."
        )
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, read_only=True, busy_timeout_ms=busy_timeout_ms)
    return conn


class Database:
    """연결 하나의 수명(열기 · 닫기)과 트랜잭션을 관리하는 작은 창구.

    ``isolation_level=None`` 으로 자동 커밋을 끄고, 트랜잭션은
    :meth:`transaction` 으로 명시한다. 암묵적 커밋이 어디서 일어나는지
    추측해야 하는 상황을 만들지 않는다.
    """

    __slots__ = ("_conn", "_read_only", "path")

    def __init__(
        self,
        path: Path | str,
        *,
        read_only: bool = False,
        busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        """[쓰임] ``read_only`` 에 따라 쓰기 연결 또는 읽기 전용 연결을 연다."""
        self.path = Path(path)
        self._read_only = read_only
        self._conn = (
            connect_read_only(self.path, busy_timeout_ms=busy_timeout_ms)
            if read_only
            else connect(self.path, busy_timeout_ms=busy_timeout_ms)
        )

    @property
    def connection(self) -> sqlite3.Connection:
        """[쓰임] 안쪽 SQLite 연결 — 저장소(repository)들이 이것으로 SQL 을 실행한다."""
        return self._conn

    @property
    def read_only(self) -> bool:
        """[쓰임] 읽기 전용으로 열었는지."""
        return self._read_only

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """[쓰임] ``with db.transaction():`` 블록을 한 트랜잭션으로 — 끝나면 커밋, 예외면 롤백.
        [이유] 여러 줄을 쓰다 중간에 실패하면 반쯤 쓴 상태가 남지 않아야 한다. 읽기 전용 연결에서는
               거부한다 — 호출 자체가 설계 오류다."""
        if self._read_only:
            raise DatabaseError("읽기 전용 연결에서는 트랜잭션을 쓸 수 없다")
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def close(self) -> None:
        """[쓰임] 연결을 닫는다."""
        self._conn.close()

    def __enter__(self) -> Database:
        """[쓰임] ``with Database(...) as db:`` 로 쓰면 블록이 끝날 때 자동으로 닫힌다."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        suffix = ", read_only=True" if self._read_only else ""
        return f"Database({self.path}{suffix})"
