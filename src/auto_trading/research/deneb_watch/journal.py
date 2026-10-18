"""연구 전용 append-only 원장. 운영 DB나 설정을 열지 않는다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research.deneb_watch.domain import (
    Entry,
    Record,
    canonical,
    entry_digest,
    visible,
)

GENESIS = "0" * 64


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                seq INTEGER PRIMARY KEY, record_key TEXT UNIQUE NOT NULL,
                recorded_ms INTEGER NOT NULL, previous_sha256 TEXT NOT NULL,
                sha256 TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON observations
                BEGIN SELECT RAISE(ABORT, 'append only'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON observations
                BEGIN SELECT RAISE(ABORT, 'append only'); END;
        """)

    def close(self) -> None:
        self.connection.close()

    def entries(self) -> list[Entry]:
        rows = self.connection.execute(
            "SELECT seq, recorded_ms, previous_sha256, sha256, payload "
            "FROM observations ORDER BY seq"
        ).fetchall()
        try:
            return [
                Entry(
                    seq=row[0],
                    recorded_ms=row[1],
                    previous_sha256=row[2],
                    sha256=row[3],
                    record=Record.model_validate_json(row[4]),
                )
                for row in rows
            ]
        except ValueError as exc:
            raise DataIntegrityError("원장 payload 형식 오류") from exc

    def verify(self) -> str:
        previous = GENESIS
        time_ms = -1
        for seq, entry in enumerate(self.entries(), 1):
            expected = entry_digest(seq, entry.recorded_ms, previous, canonical(entry.record))
            if (
                entry.seq != seq
                or entry.previous_sha256 != previous
                or entry.sha256 != expected
                or entry.recorded_ms < time_ms
            ):
                raise DataIntegrityError("원장 해시/순서 불일치")
            previous, time_ms = entry.sha256, entry.recorded_ms
        keys = self.connection.execute("SELECT record_key, payload FROM observations").fetchall()
        if any(key != Record.model_validate_json(payload).key for key, payload in keys):
            raise DataIntegrityError("원장 ID 불일치")
        return previous

    def append(self, record: Record, *, recorded_ms: int) -> Entry:
        if recorded_ms < 0:
            raise CausalityError("음수 시각")
        if any(
            value is not None and value > recorded_ms
            for value in (record.observed_ms, record.published_ms, record.claimed_action_ms)
        ):
            raise CausalityError("아직 발생/공개되지 않은 사실 또는 행동")
        payload = canonical(record)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            previous = self.verify()
            old = self.connection.execute(
                "SELECT payload FROM observations WHERE record_key=?", (record.key,)
            ).fetchone()
            if old:
                if old[0] != payload:
                    raise DataIntegrityError("동일 ID 내용 변경 거부: 정정은 새 ID로 추가")
                entry = next(item for item in self.entries() if item.record.key == record.key)
                self.connection.rollback()
                return entry
            entries = self.entries()
            known = {entry.record.key for entry in entries}
            if any(key not in known for key in record.related_keys):
                raise DataIntegrityError("근거 참조가 먼저 기록되지 않음")
            if entries and recorded_ms < entries[-1].recorded_ms:
                raise CausalityError("원장 수집 시각 소급 거부")
            seq = len(entries) + 1
            digest = entry_digest(seq, recorded_ms, previous, payload)
            self.connection.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
                (seq, record.key, recorded_ms, previous, digest, payload),
            )
            self.connection.commit()
            return Entry(
                seq=seq,
                recorded_ms=recorded_ms,
                previous_sha256=previous,
                sha256=digest,
                record=record,
            )
        except (sqlite3.Error, DataIntegrityError, CausalityError):
            self.connection.rollback()
            raise

    def as_of(self, as_of_ms: int) -> list[Entry]:
        self.verify()
        return [entry for entry in self.entries() if visible(entry, as_of_ms)]
