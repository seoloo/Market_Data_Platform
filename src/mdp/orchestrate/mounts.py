"""Spark 컨테이너에 붙일 **호스트** 경로 찾기 — Airflow 컨테이너 자신의 연결 정보를 읽는다.

언제 쓰나: DAG 가 DockerOperator(Spark 작업)를 만들 때.
읽고 쓰는 것: Docker 엔진의 '지금 컨테이너' 정보(읽기만).

[이유] DockerOperator 가 띄우는 Spark 컨테이너는 Airflow 안이 아니라 **호스트의 Docker** 가
       만든다. 그래서 폴더를 붙일 때 Airflow 안 경로(/opt/mdp/lake)가 아니라 호스트 경로
       (C:\\…\\data\\lake, F:\\mdp\\bronze)를 줘야 한다. 그 경로를 설정에 따로 적으면 PC 마다
       고쳐야 하므로, Airflow 컨테이너가 붙어 있는 연결 정보를 Docker 에게 물어
       '이 안 경로는 호스트의 어디인가' 를 찾는다.
"""

from __future__ import annotations

import importlib
import socket
from collections.abc import Iterable, Mapping
from typing import Any, Final

__all__ = ["AIRFLOW_TO_SPARK", "host_sources", "spark_mounts"]

AIRFLOW_TO_SPARK: Final = {
    "/opt/mdp/src": ("/app/src", True),
    "/opt/mdp/manifests": ("/app/manifests", True),
    "/opt/mdp/lake": ("/lake", False),
    "/opt/mdp/bronze": ("/lake/bronze", True),
}
"""Airflow 안 경로 → (Spark 컨테이너 안 경로, 읽기만?) — compose 의 spark 서비스와 같은 배치."""


def host_sources(mounts: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """[쓰임] Docker 의 컨테이너 ``Mounts`` 정보 → {컨테이너 안 경로: 호스트 경로} (bind 만)."""
    return {
        str(m["Destination"]): str(m["Source"])
        for m in mounts
        if m.get("Type") == "bind" and m.get("Source")
    }


def spark_mounts(
    sources: Mapping[str, str], staging_volume: str
) -> list[tuple[str, str, str, bool]]:
    """[쓰임] Spark 컨테이너에 붙일 목록 ``(호스트 경로 · 볼륨, 안 경로, 종류, 읽기만)``.
    [이유] 필요한 연결이 하나라도 없으면 Spark 가 엉뚱한 곳에 쓰지 않게 바로 멈춘다."""
    missing = [inside for inside in AIRFLOW_TO_SPARK if inside not in sources]
    if missing:
        raise RuntimeError(f"Airflow 컨테이너에 이 연결이 없다: {missing}")
    mounts = [
        (sources[inside], target, "bind", read_only)
        for inside, (target, read_only) in AIRFLOW_TO_SPARK.items()
    ]
    mounts.append((staging_volume, "/staging", "volume", False))
    return mounts


def current_container_mounts() -> list[dict[str, Any]]:
    """[쓰임] 지금 돌고 있는 컨테이너(호스트 이름 = 컨테이너 번호)의 ``Mounts`` 정보."""
    # 저장소의 docker/ 폴더(이미지 정의)를 mypy 가 같은 이름 패키지로 잡아서 이름으로 불러온다.
    docker: Any = importlib.import_module("docker")

    client = docker.from_env()
    try:
        container = client.containers.get(socket.gethostname())
        mounts: list[dict[str, Any]] = container.attrs.get("Mounts", [])
        return mounts
    finally:
        client.close()
