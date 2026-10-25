"""두 DAG(월 파이프라인 · 매일 대사)가 함께 쓰는 Spark 작업 만들기 — DAG 가 아니라 도우미다.

[이유] Spark 컨테이너를 띄우는 설정(이미지 · 붙일 폴더 · 환경 변수 · 시간 제한)이 DAG 마다 갈라지면
       한쪽만 고쳐지는 일이 생긴다. 한 곳에 둔다.
"""

from __future__ import annotations

import os
from datetime import timedelta
from functools import lru_cache

from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

from mdp.orchestrate.mounts import current_container_mounts, host_sources, spark_mounts

SPARK_IMAGE = "mdp-spark:4.0.1-delta4.0.1"
STAGING_VOLUME = "mdp_mdp_staging"
COMPOSE_NETWORK = "mdp_default"
"""Kafka(kafka:9092)에 닿아야 하는 작업(스트림 · 대사)이 붙는 compose 네트워크."""


@lru_cache(maxsize=1)
def _spark_mounts() -> list[Mount]:
    """[쓰임] Spark 컨테이너에 붙일 호스트 경로(Airflow 컨테이너 연결 정보에서 찾음).
    DAG 를 읽을 때 한 번."""
    sources = host_sources(current_container_mounts())
    return [
        Mount(target=target, source=source, type=kind, read_only=read_only)
        for source, target, kind, read_only in spark_mounts(sources, STAGING_VOLUME)
    ]


def spark_task(task_id: str, command: str, *, kafka: bool = False) -> DockerOperator:
    """[쓰임] Spark 이미지로 우리 명령 하나를 돌리는 작업 (compose 의 spark 서비스와 같은 환경).
    ``kafka`` 면 compose 네트워크에 붙여 ``kafka:9092`` 에 닿게 한다."""
    environment = {
        "MDP_BRONZE_ROOT": "/lake/bronze",
        "MDP_LAKE_ROOT": "/lake",
        "MDP_STAGING_ROOT": "/staging",
        "MDP_PG_DSN": os.environ.get("MDP_SPARK_PG_DSN", ""),
        "MDP_SPARK_MEMORY": os.environ.get("MDP_SPARK_MEMORY", "6g"),
        "MDP_SPARK_CORES": os.environ.get("MDP_SPARK_CORES", "8"),
    }
    if kafka:
        environment["MDP_KAFKA_BOOTSTRAP"] = "kafka:9092"
    return DockerOperator(
        task_id=task_id,
        image=SPARK_IMAGE,
        command=command,
        docker_url="unix://var/run/docker.sock",
        network_mode=COMPOSE_NETWORK if kafka else "bridge",
        mounts=_spark_mounts(),
        mount_tmp_dir=False,
        auto_remove="success",
        extra_hosts={"host.docker.internal": "host-gateway"},
        environment=environment,
        execution_timeout=timedelta(hours=1),
    )
