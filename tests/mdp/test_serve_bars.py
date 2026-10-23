"""Gold → PostgreSQL 복사 시험.

- Delta 버전 읽기는 늘 돈다.
- 실제 복사 · 체크섬은 PostgreSQL(``MDP_TEST_PG_DSN``)과 실제 Gold 표(``data/lake/gold/bars_1m``)가
  모두 있을 때만 돈다. 시험마다 새 스키마에 써서 실제 조회용 표를 건드리지 않는다.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import pytest

from mdp.serve.bars_1m import latest_delta_version


def test_latest_delta_version_reads_highest_log_number(tmp_path: Path) -> None:
    log = tmp_path / "_delta_log"
    log.mkdir()
    for name in ("00000000000000000000.json", "00000000000000000003.json", "_last_checkpoint"):
        (log / name).write_text("{}", encoding="utf-8")
    assert latest_delta_version(tmp_path) == 3
    with pytest.raises(FileNotFoundError):
        latest_delta_version(tmp_path / "missing")


GOLD = Path(__file__).resolve().parents[2] / "data" / "lake" / "gold" / "bars_1m"
BASE_DSN = os.environ.get("MDP_TEST_PG_DSN")


@pytest.mark.postgres
@pytest.mark.skipif(
    not BASE_DSN or not (GOLD / "_delta_log").is_dir(), reason="PostgreSQL 또는 실제 Gold 없음"
)
def test_copy_is_idempotent_and_checksums_match() -> None:
    psycopg = pytest.importorskip("psycopg")
    pytest.importorskip("duckdb")
    from psycopg.conninfo import make_conninfo

    from mdp.serve.bars_1m import copy_month

    assert BASE_DSN is not None
    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(BASE_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            dsn = make_conninfo(BASE_DSN, options=f"-c search_path={schema}")
            first = copy_month("BTCUSDT", date(2024, 3, 1), dsn, str(uuid.uuid4()), schema)
            second = copy_month("BTCUSDT", date(2024, 3, 1), dsn, str(uuid.uuid4()), schema)
            assert first.rows > 0 and first.delta_sha256 == first.postgres_sha256
            assert (second.rows, second.postgres_sha256) == (first.rows, first.postgres_sha256)
            count = admin.execute(f'SELECT count(*) FROM "{schema}".gold_bars_1m').fetchone()
            assert count is not None and count[0] == first.rows  # 두 번 넣어도 한 벌
        finally:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
