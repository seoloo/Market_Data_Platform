"""재현성 고정점 저장소 — 설정·코드 버전.

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
    id: int
    hash: str
    created_ms: int


@dataclass(frozen=True, slots=True)
class CodeVersion:
    id: int
    git_sha: str
    dirty: bool
    created_ms: int

    @property
    def allows_live_trading(self) -> bool:
        """커밋되지 않은 변경이 있으면 실거래를 금지한다 (게이트 G1.4).

        재현할 수 없는 코드로 실거래를 하면 사후에 무슨 일이 있었는지 알 수 없다.
        """
        return not self.dirty


class MetaRepository:
    """설정·코드 버전을 등록하고 조회한다."""

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
        """설정 버전을 등록한다. 같은 해시면 기존 행을 재사용한다 (멱등).

        재기동할 때마다 같은 설정으로 새 행이 생기면 이력이 무의미해진다.
        """
        existing = self.get_config_by_hash(config_hash)
        if existing is not None:
            return existing
        cursor = self._conn.execute(
            "INSERT INTO config_versions (hash, config_json, created_ms, note) VALUES (?, ?, ?, ?)",
            (config_hash, config_json, created_ms, note),
        )
        return ConfigVersion(id=int(cursor.lastrowid or 0), hash=config_hash, created_ms=created_ms)

    def get_config_by_hash(self, config_hash: str) -> ConfigVersion | None:
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
        """과거 결정을 재현할 때 그 시점의 설정 원문을 꺼낸다."""
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
        """코드 버전을 등록한다. 같은 (sha, dirty, deps) 조합이면 재사용한다."""
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
