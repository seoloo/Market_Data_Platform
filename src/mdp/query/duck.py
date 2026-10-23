"""DuckDB 연결과 Delta 표 뷰.

언제 쓰나: 조회 · 검산 명령이 시작할 때 :func:`connect` 한 번, 이어서 :func:`register_views`.
읽고 쓰는 것: ``<lake>/silver/*`` · ``<lake>/gold/*`` Delta 표(읽기만). DuckDB 는 메모리에서만 연다
              (파일을 만들지 않음). 처음 한 번 DuckDB 공식 저장소에서 delta 확장을 받는다.

뷰 이름 규칙: ``<계층>_<표>`` — 예 ``silver_agg_trades`` · ``silver_agg_trades_audit_daily``
(늘 최신 버전). 이전 버전(시간여행)은 표마다 버전 번호가 달라 뷰로 묶지 않고
:func:`delta_source` 에 ``version`` 을 준다 —
예 ``SELECT count(*) FROM {delta_source("silver", "agg_trades", 0)}`` 는 그 표를 처음 쓴 상태.

[이유] 표 위치는 Spark 쪽과 같은 규칙(:func:`mdp.spark.session.table_path`)을 써서, 두 엔진이 언제나
       같은 표를 본다.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from mdp.spark.session import lake_root, table_path

if TYPE_CHECKING:
    import duckdb

__all__ = ["SILVER_TABLES", "connect", "delta_source", "register_views"]

SILVER_TABLES: Final = (
    "agg_trades",
    "agg_trades_quarantine",
    "agg_trades_audit_file",
    "agg_trades_gaps",
    "agg_trades_audit_daily",
)


def connect(
    threads: int | None = None, memory_limit: str | None = None
) -> duckdb.DuckDBPyConnection:
    """[쓰임] 메모리 안 DuckDB 를 열고 delta 확장을 켠다. 스레드 · 메모리 한도는 환경 변수
              ``MDP_DUCKDB_THREADS`` · ``MDP_DUCKDB_MEMORY`` 로도 정한다.
    [이유] 단일 PC 에서 Spark · PostgreSQL 과 함께 돌 수 있게 메모리 한도를 둔다(기본 8 GB)."""
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL delta")  # 이미 받았으면 아무 일도 하지 않는다
    con.execute("LOAD delta")
    thread_count = threads or int(os.environ.get("MDP_DUCKDB_THREADS", "8"))
    con.execute(f"SET threads = {thread_count}")
    limit = memory_limit or os.environ.get("MDP_DUCKDB_MEMORY", "8GB")
    con.execute(f"SET memory_limit = '{limit}'")
    return con


def delta_source(layer: str, name: str, version: int | None = None) -> str:
    """[쓰임] Delta 표 하나를 읽는 SQL 조각 — ``delta_scan('경로')`` (버전을 주면 그 버전).
    [이유] 경로의 역슬래시 · 따옴표를 한 곳에서 정리해, SQL 에 경로를 그대로 끼우는 실수를
           막는다."""
    path = table_path(layer, name).replace("\\", "/").replace("'", "''")
    if version is None:
        return f"delta_scan('{path}')"
    return f"delta_scan('{path}', version => {int(version)})"


def register_views(con: duckdb.DuckDBPyConnection, layer: str = "silver") -> list[str]:
    """[쓰임] 레이크에 있는 그 계층의 Delta 표를 모두 ``<계층>_<표>`` 뷰로 등록하고 이름 목록을
              돌려준다.
    [이유] 아직 만들지 않은 표(폴더 없음)는 건너뛴다 — 뷰가 실패해 전체 조회가 막히지 않게."""
    folder = lake_root() / layer
    registered: list[str] = []
    if not folder.exists():
        return registered
    for table_dir in sorted(folder.iterdir()):
        if not (table_dir / "_delta_log").is_dir():
            continue
        view = f"{layer}_{table_dir.name}"
        con.execute(
            f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {delta_source(layer, table_dir.name)}"
        )
        registered.append(view)
    return registered
