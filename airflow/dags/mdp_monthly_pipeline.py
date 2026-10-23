"""MDP 월 파이프라인 — 받기 → Silver → 검산 → Gold → PostgreSQL 복사 (README 4-6).

언제 도나:
- 예약: ``@monthly`` — 달이 끝나면 그 달을 돌린다(3 월 구간 [3/1, 4/1)은 4/1 에).
  처음에는 꺼져 있다.
- 수동: Airflow 화면 · ``airflow dags trigger mdp_monthly_pipeline -c '{"month": "2024-03"}'``.
- 백필: ``airflow backfill create --dag-id mdp_monthly_pipeline``
  ``--from-date 2024-03-01 --to-date 2024-05-01``.

작업 (각 단계는 평소와 **같은 명령**을 부른다 — Airflow 전용 코드를 따로 두지 않는다):

| 작업 | 어디서 | 명령 |
|---|---|---|
| ``bronze_trades`` · ``bronze_klines`` | Airflow 안 | ``python -m mdp.bronze.sync`` (둘은 동시에) |
| ``silver`` | Spark 컨테이너 (DockerOperator) | ``python -m mdp.silver.agg_trades`` |
| ``check_silver`` | Airflow 안 | ``python -m mdp.query.check_silver`` (실패하면 멈춤) |
| ``gold`` | Spark 컨테이너 | ``python -m mdp.gold.bars_1m`` |
| ``serve`` | Airflow 안 | ``python -m mdp.serve.bars_1m`` |

실패 처리: 작업마다 2 번 다시 시도(5 분 간격), 한 번에 한 달만
(``max_active_runs=1`` — 단일 PC 메모리).
Spark 작업은 실행 시간 제한 1 시간.

[이유] 각 명령이 이미 멱등(같은 달을 다시 돌려도 결과가 같음)이고 실패하면 종료 코드가
       0 이 아니므로, Airflow 는 '순서 · 다시 시도 · 기록' 만 맡는다. 다시 시도해도
       안전한 것은 각 명령이 멱등이기 때문이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from airflow.providers.docker.operators.docker import DockerOperator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param
from docker.types import Mount

from mdp.orchestrate.mounts import current_container_mounts, host_sources, spark_mounts
from mdp.orchestrate.window import mdp_month, mdp_month_end, mdp_month_start

SPARK_IMAGE = "mdp-spark:4.0.1-delta4.0.1"
STAGING_VOLUME = "mdp_mdp_staging"
MONTH = "{{ mdp_month(params, data_interval_start) }}"
COMMON = f"--symbol {{{{ params.symbol }}}} --month {MONTH}"


@lru_cache(maxsize=1)
def _spark_mounts() -> list[Mount]:
    """[쓰임] Spark 컨테이너에 붙일 호스트 경로(Airflow 컨테이너 연결 정보에서 찾음).
    DAG 를 읽을 때 한 번."""
    sources = host_sources(current_container_mounts())
    return [
        Mount(target=target, source=source, type=kind, read_only=read_only)
        for source, target, kind, read_only in spark_mounts(sources, STAGING_VOLUME)
    ]


def _spark_task(task_id: str, module: str) -> DockerOperator:
    """[쓰임] Spark 이미지로 우리 명령 하나를 돌리는 작업 (compose 의 spark 서비스와 같은 환경)."""
    import os

    return DockerOperator(
        task_id=task_id,
        image=SPARK_IMAGE,
        command=f"python -m {module} {COMMON}",
        docker_url="unix://var/run/docker.sock",
        network_mode="bridge",
        mounts=_spark_mounts(),
        mount_tmp_dir=False,
        auto_remove="success",
        extra_hosts={"host.docker.internal": "host-gateway"},
        environment={
            "MDP_BRONZE_ROOT": "/lake/bronze",
            "MDP_LAKE_ROOT": "/lake",
            "MDP_STAGING_ROOT": "/staging",
            "MDP_PG_DSN": os.environ.get("MDP_SPARK_PG_DSN", ""),
            "MDP_SPARK_MEMORY": os.environ.get("MDP_SPARK_MEMORY", "6g"),
            "MDP_SPARK_CORES": os.environ.get("MDP_SPARK_CORES", "8"),
        },
        execution_timeout=timedelta(hours=1),
    )


default_args: dict[str, Any] = {
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="mdp_monthly_pipeline",
    description="받기 → Silver → 검산 → Gold → PostgreSQL 복사 (한 달)",
    schedule="@monthly",
    start_date=datetime(2024, 3, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    params={
        "symbol": Param("BTCUSDT", type="string", description="종목"),
        "month": Param("", type="string", description="YYYY-MM (비우면 예약 구간의 달)"),
    },
    user_defined_macros={
        "mdp_month": mdp_month,
        "mdp_month_start": mdp_month_start,
        "mdp_month_end": mdp_month_end,
    },
    tags=["mdp", "lakehouse"],
) as dag:
    span = (
        "--start {{ mdp_month_start(params, data_interval_start) }}"
        " --end {{ mdp_month_end(params, data_interval_start) }}"
    )
    bronze_trades = BashOperator(
        task_id="bronze_trades",
        bash_command=(
            f"python -m mdp.bronze.sync --dataset aggTrades --symbol {{{{ params.symbol }}}} {span}"
        ),
    )
    bronze_klines = BashOperator(
        task_id="bronze_klines",
        bash_command=(
            "python -m mdp.bronze.sync --dataset klines --interval 1m"
            f" --symbol {{{{ params.symbol }}}} {span}"
        ),
    )
    silver = _spark_task("silver", "mdp.silver.agg_trades")
    check_silver = BashOperator(
        task_id="check_silver", bash_command=f"python -m mdp.query.check_silver {COMMON}"
    )
    gold = _spark_task("gold", "mdp.gold.bars_1m")
    serve = BashOperator(task_id="serve", bash_command=f"python -m mdp.serve.bars_1m {COMMON}")

    [bronze_trades, bronze_klines] >> silver >> check_silver >> gold >> serve
