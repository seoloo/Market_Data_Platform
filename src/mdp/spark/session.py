"""Delta Lake 를 켠 로컬 Spark 세션과 레이크 경로.

언제 쓰나: Silver · Gold 작업이 시작할 때 :func:`spark_session` 한 번.
읽고 쓰는 것: 환경 변수 ``MDP_LAKE_ROOT`` · ``MDP_SPARK_MEMORY`` · ``MDP_SPARK_CORES`` (읽기만).

[이유] 단일 PC 라 ``local[N]`` 모드 — 드라이버 하나가 N 개 스레드로 일한다. 클러스터처럼 꾸미지
       않는다 (README 그림 2 '단일 PC 그대로'). 셔플 파티션 수를 기본 200 에서 코어 수 근처로 줄인다
       — 한 달치 자료에 200 조각은 작은 파일만 잔뜩 만든다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

__all__ = ["lake_root", "spark_session", "table_path"]

_DEFAULT_LAKE: Final = Path(__file__).resolve().parents[3] / "data" / "lake"


def lake_root() -> Path:
    """[쓰임] 레이크 맨 위 폴더 — ``MDP_LAKE_ROOT`` 또는 ``<저장소>/data/lake`` (Git 밖)."""
    from_env = os.environ.get("MDP_LAKE_ROOT")
    return Path(from_env) if from_env else _DEFAULT_LAKE


def table_path(layer: str, name: str) -> str:
    """[쓰임] 계층 · 표 이름 → Delta 표 폴더 경로 문자열 (예: ``/lake/silver/agg_trades``).
    [이유] 표 위치 규칙을 한 곳에 — Silver · Gold · DuckDB 조회(4-2)가 같은 경로를 쓴다."""
    if layer not in {"silver", "gold"}:
        raise ValueError(f"Delta 표 계층은 silver · gold 만: {layer}")
    return str(lake_root() / layer / name)


def spark_session(app_name: str) -> SparkSession:
    """[쓰임] Delta 를 켠 로컬 Spark 세션. 이미 있으면 그것을 돌려준다.
    [이유] Delta 설정 두 줄(SQL 확장 · 카탈로그)이 빠지면 ``format("delta")`` 가 실패한다 — 작업마다
           따로 적지 않게 여기 모았다. 시간대는 UTC 로 고정 — 원천 시각이 UTC ms 라 날짜 나누기가 PC
           시간대에 따라 달라지면 안 된다."""
    from pyspark.sql import SparkSession

    cores = os.environ.get("MDP_SPARK_CORES", "8")
    builder = (
        SparkSession.builder.appName(app_name)
        .master(f"local[{cores}]")
        .config("spark.driver.memory", os.environ.get("MDP_SPARK_MEMORY", "6g"))
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", str(int(cores) * 2))
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")  # 진행 막대가 JSON 로그를 가린다
    )
    return builder.getOrCreate()
