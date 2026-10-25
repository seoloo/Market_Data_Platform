"""오케스트레이션 도우미 시험 — 실행할 달 정하기 · Spark 컨테이너 연결 목록.

Airflow · Docker 없이 돈다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mdp.orchestrate.mounts import AIRFLOW_TO_SPARK, host_sources, spark_mounts
from mdp.orchestrate.window import mdp_month, mdp_month_end, mdp_month_start

MARCH = datetime(2024, 3, 1, tzinfo=UTC)


def test_month_param_wins_over_interval() -> None:
    params = {"month": "2024-02"}
    assert mdp_month(params, MARCH) == "2024-02"
    assert mdp_month_start(params, MARCH) == "2024-02-01"
    assert mdp_month_end(params, MARCH) == "2024-02-29"  # 윤년


def test_month_falls_back_to_interval_start() -> None:
    for params in ({}, {"month": ""}, {"month": "  "}):
        assert mdp_month(params, datetime(2024, 3, 17, 5, tzinfo=UTC)) == "2024-03"
    assert mdp_month_end({}, MARCH) == "2024-03-31"
    assert mdp_month_end({}, datetime(2024, 12, 1, tzinfo=UTC)) == "2024-12-31"  # 해 넘김


def test_month_unknown_stops() -> None:
    with pytest.raises(ValueError, match="month"):
        mdp_month({}, None)


def _bind(destination: str, source: str) -> dict[str, str]:
    return {"Type": "bind", "Destination": destination, "Source": source}


def test_host_sources_keeps_bind_mounts_only() -> None:
    mounts = [
        _bind("/opt/mdp/lake", "C:\\repo\\data\\lake"),
        {"Type": "volume", "Destination": "/opt/airflow/logs", "Source": "/var/lib/docker/v"},
        {"Type": "bind", "Destination": "/empty", "Source": ""},
    ]
    assert host_sources(mounts) == {"/opt/mdp/lake": "C:\\repo\\data\\lake"}


def test_spark_mounts_maps_every_path_and_adds_staging() -> None:
    sources = {inside: f"H:{inside}" for inside in AIRFLOW_TO_SPARK}
    mounts = spark_mounts(sources, "mdp_mdp_staging")
    assert ("H:/opt/mdp/lake", "/lake", "bind", False) in mounts
    assert ("H:/opt/mdp/bronze", "/lake/bronze", "bind", True) in mounts  # Bronze 는 읽기만
    assert mounts[-1] == ("mdp_mdp_staging", "/staging", "volume", False)
    assert len(mounts) == len(AIRFLOW_TO_SPARK) + 1


def test_spark_mounts_missing_path_stops() -> None:
    sources = {inside: "H:" for inside in AIRFLOW_TO_SPARK if inside != "/opt/mdp/bronze"}
    with pytest.raises(RuntimeError, match="/opt/mdp/bronze"):
        spark_mounts(sources, "mdp_mdp_staging")


def test_day_param_wins_and_falls_back_to_interval() -> None:
    from mdp.orchestrate.window import mdp_day, mdp_day_month

    assert mdp_day({"day": "2026-10-25"}, MARCH) == "2026-10-25"
    assert mdp_day({}, datetime(2026, 10, 25, 0, tzinfo=UTC)) == "2026-10-25"
    assert mdp_day_month({"day": "2026-10-25"}, None) == "2026-10"
    with pytest.raises(ValueError, match="day"):
        mdp_day({"day": ""}, None)
