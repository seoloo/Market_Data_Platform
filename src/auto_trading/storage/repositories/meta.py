"""재현성 고정점 저장소 — 설정 · 코드 버전 (``config_versions`` · ``code_versions`` 표).

언제 쓰나: 지금은 이 저장소를 부르는 실행 코드가 없고 시험(tests/storage)만 쓴다 — 실행할 때의
           설정 · 코드 버전을 남겨 두는 자리로 만들어 두었다.
읽고 쓰는 것: ``config_versions`` · ``code_versions`` 표.

"왜 그 거래가 발생했는가"를 재구성하려면 결정 시점의 **설정·코드·입력**이 모두
고정되어야 한다 (03-data-model.md §2). 이 모듈은 앞의 두 개를 담당한다.

세 번째(입력)는 ``decisions.snapshot_digest`` 가 담당한다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from auto_trading.core.hashing import canonical_json

__all__ = ["CodeVersion", "ConfigVersion", "MetaRepository"]


@dataclass(frozen=True, slots=True)
class ConfigVersion:
    """등록된 설정 하나 (번호 · 설정 해시 · 등록 시각)."""

    id: int
    hash: str
    created_ms: int


@dataclass(frozen=True, slots=True)
class CodeVersion:
    """등록된 코드 상태 하나 (번호 · git 커밋 · 커밋 안 된 변경이 있었는지 · 등록 시각)."""

    id: int
    git_sha: str
    dirty: bool
    created_ms: int

    @property
    def allows_live_trading(self) -> bool:
        """[쓰임] 커밋되지 않은 변경이 없을 때만 True (게이트 G1.4).
        [이유] 재현할 수 없는 코드로 돌리면 사후에 무슨 일이 있었는지 알 수 없다."""
        return not self.dirty


class MetaRepository:
    """설정 · 코드 버전을 등록하고 조회하는 창구."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------- 설정

    def register_config(
        self,
        *,
        config_hash: str,
        config_json: str,
        created_ms: int,
        note: str | None = None,
    ) -> ConfigVersion:
        """[쓰임] 설정을 등록하고 그 번호를 돌려준다. 같은 해시가 이미 있으면 그 줄을 그대로 쓴다.
        [이유] 재기동할 때마다 같은 설정으로 새 줄이 생기면 이력이 무의미해진다 (멱등)."""
        existing = self.get_config_by_hash(config_hash)
        if existing is not None:
            return existing
        cursor = self._conn.execute(
            "INSERT INTO config_versions (hash, config_json, created_ms, note) VALUES (?, ?, ?, ?)",
            (config_hash, config_json, created_ms, note),
        )
        return ConfigVersion(id=int(cursor.lastrowid or 0), hash=config_hash, created_ms=created_ms)

    def get_config_by_hash(self, config_hash: str) -> ConfigVersion | None:
        """[쓰임] 설정 해시로 등록된 설정을 찾는다 (없으면 None)."""
        row = self._conn.execute(
            "SELECT id, hash, created_ms FROM config_versions WHERE hash = ?",
            (config_hash,),
        ).fetchone()
        if row is None:
            return None
        return ConfigVersion(
            id=int(row["id"]), hash=str(row["hash"]), created_ms=int(row["created_ms"])
        )

    def get_config_json(self, config_version_id: int) -> str | None:
        """[쓰임] 설정 번호로 그때의 설정 원문(JSON)을 꺼낸다 — 과거 결정을 재현할 때."""
        row = self._conn.execute(
            "SELECT config_json FROM config_versions WHERE id = ?", (config_version_id,)
        ).fetchone()
        return None if row is None else str(row["config_json"])

    # ------------------------------------------------------------- 코드

    def register_code(
        self,
        *,
        git_sha: str,
        dirty: bool,
        deps: dict[str, str],
        created_ms: int,
    ) -> CodeVersion:
        """[쓰임] 코드 상태(커밋 · 변경 여부 · 의존성 버전)를 등록하고 번호를 돌려준다.
        같은 조합이 이미 있으면 그 번호를 그대로 쓴다.
        [이유] 의존성은 :func:`canonical_json` 으로 바꿔 저장한다 — 키 순서만 달라도 다른 줄이
               생기는 일을 막는다."""
        deps_json = canonical_json(deps)
        row = self._conn.execute(
            "SELECT id, created_ms FROM code_versions"
            " WHERE git_sha = ? AND dirty = ? AND deps_json = ?",
            (git_sha, int(dirty), deps_json),
        ).fetchone()
        if row is not None:
            return CodeVersion(
                id=int(row["id"]),
                git_sha=git_sha,
                dirty=dirty,
                created_ms=int(row["created_ms"]),
            )
        cursor = self._conn.execute(
            "INSERT INTO code_versions (git_sha, dirty, deps_json, created_ms) VALUES (?, ?, ?, ?)",
            (git_sha, int(dirty), deps_json, created_ms),
        )
        return CodeVersion(
            id=int(cursor.lastrowid or 0), git_sha=git_sha, dirty=dirty, created_ms=created_ms
        )

    def get_code(self, code_version_id: int) -> CodeVersion | None:
        """[쓰임] 코드 번호로 등록된 코드 상태를 찾는다 (없으면 None)."""
        row = self._conn.execute(
            "SELECT id, git_sha, dirty, created_ms FROM code_versions WHERE id = ?",
            (code_version_id,),
        ).fetchone()
        if row is None:
            return None
        return CodeVersion(
            id=int(row["id"]),
            git_sha=str(row["git_sha"]),
            dirty=bool(row["dirty"]),
            created_ms=int(row["created_ms"]),
        )
