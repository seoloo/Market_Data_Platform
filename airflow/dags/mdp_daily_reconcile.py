"""MDP 매일 대사 — 그날 일 파일 → Silver · Gold(그달) → 스트림 봉 → 대사 · 메우기 → 상태 화면.

README 5-2 를 매일 자동으로.

언제 도나:
- 예약: ``@daily`` — D 일 구간 [D, D+1)을 D+1 에 돌린다. 바이낸스는 D 일 일 파일을 다음 날 올리므로
  받기 작업은 '아직 없음' 이면 실패하고 1 시간마다 다시 시도한다(최대 하루). 처음에는 꺼져 있다.
- 수동: ``airflow dags trigger mdp_daily_reconcile -c '{"day": "2026-10-25"}'``.

작업 (평소와 **같은 명령**을 부른다):

| 작업 | 어디서 | 명령 |
|---|---|---|
| ``bronze_trades`` · ``bronze_klines`` | Airflow 안 | ``bronze.sync --days D --require-all`` |
| ``silver`` · ``gold`` | Spark 컨테이너 | 그날이 속한 달을 다시 (달 단위 바꿔 끼우기) |
| ``check_silver`` | Airflow 안 | DuckDB 검산 (실패하면 멈춤) |
| ``stream`` | Spark · compose 네트워크 | ``stream.bars_1m --trigger available-now`` |
| ``reconcile`` | Spark · compose 네트워크 | ``reconcile.daily --date D --repair`` |
| ``report`` | Airflow 안 | ``ui.report`` (상태 화면) |

[이유] 스트림 봉은 원문 토픽에서 '그때까지 받은 것' 을 한 번에 확정한다(``available-now``) —
       계속 도는 스트림 작업을 PC 에 늘 띄워 두지 않으려고. 받기는
       ``mdp.stream.produce --source live`` 가 따로 돌며 원문을 쌓는다(받은 시각이 원문에 남아
       신선도는 그대로 잴 수 있다). 체크포인트 이름을 고정해 날마다 지난번 다음 메시지부터 읽는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param
from mdp_dag_common import spark_task

from mdp.orchestrate.window import mdp_day, mdp_day_month

DAY = "{{ mdp_day(params, data_interval_start) }}"
MONTH = "{{ mdp_day_month(params, data_interval_start) }}"
SYMBOL = "{{ params.symbol }}"
WAIT_FOR_ARCHIVE: dict[str, Any] = {"retries": 24, "retry_delay": timedelta(hours=1)}
"""일 파일이 올라올 때까지 — 1 시간마다, 최대 하루 (올라오는 시각은 확인 필요)."""

with DAG(
    dag_id="mdp_daily_reconcile",
    description="그날 일 파일 → Silver · Gold → 스트림 봉 → 대사 · 메우기 → 상태 화면",
    schedule="@daily",
    start_date=datetime(2026, 10, 25, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={
        "symbol": Param("BTCUSDT", type="string", description="종목"),
        "day": Param("", type="string", description="YYYY-MM-DD (비우면 예약 구간의 날)"),
    },
    user_defined_macros={"mdp_day": mdp_day, "mdp_day_month": mdp_day_month},
    tags=["mdp", "stream", "reconcile"],
) as dag:
    bronze_trades = BashOperator(
        task_id="bronze_trades",
        bash_command=(
            f"python -m mdp.bronze.sync --dataset aggTrades --symbol {SYMBOL} --days {DAY}"
            " --require-all"
        ),
        **WAIT_FOR_ARCHIVE,
    )
    bronze_klines = BashOperator(
        task_id="bronze_klines",
        bash_command=(
            "python -m mdp.bronze.sync --dataset klines --interval 1m"
            f" --symbol {SYMBOL} --days {DAY} --require-all"
        ),
        **WAIT_FOR_ARCHIVE,
    )
    month = f"--symbol {SYMBOL} --month {MONTH}"
    silver = spark_task("silver", f"python -m mdp.silver.agg_trades {month}")
    check_silver = BashOperator(
        task_id="check_silver", bash_command=f"python -m mdp.query.check_silver {month}"
    )
    gold = spark_task("gold", f"python -m mdp.gold.bars_1m {month}")
    stream = spark_task(
        "stream",
        "python -m mdp.stream.bars_1m --trigger available-now --checkpoint-name daily",
        kafka=True,
    )
    reconcile = spark_task(
        "reconcile",
        f"python -m mdp.reconcile.daily --symbol {SYMBOL} --date {DAY} --repair",
        kafka=True,
    )
    report = BashOperator(task_id="report", bash_command="python -m mdp.ui.report")

    (
        [bronze_trades, bronze_klines]
        >> silver
        >> check_silver
        >> gold
        >> stream
        >> reconcile
        >> report
    )
