# 0008 — Airflow는 순서 · 다시 시도만, 계산은 기존 명령

- 상태: 채택 · 2026-10-23
- 관련: [Airflow 기록](../AIRFLOW.md) · [DAG](../../airflow/dags/mdp_monthly_pipeline.py)

## 맥락

명령 6개(받기 2 · Silver · 검산 · Gold · 복사)를 사람이 순서대로 쳤다. 순서 · 실패하면 멈추기 · 다시 시도 · 매달
자동 실행을 코드로 옮기고 싶었다.

## 결정

- DAG의 작업은 **평소와 같은 명령**을 부른다(`python -m mdp...`). Airflow 전용 처리 코드는 두지 않는다.
- 무거운 Spark 단계는 DockerOperator로 Spark 이미지를 띄운다. 붙일 **호스트 경로는 Airflow 컨테이너가 자기 연결
  정보를 Docker에게 물어서** 찾는다(설정 파일에 PC마다 경로를 적지 않음).
- 돌릴 달은 한 함수가 정한다: 매개변수 `month` 우선, 없으면 예약 구간의 달, 둘 다 없으면 멈춤.
- 작업마다 2번 다시 시도 · Spark 1시간 제한 · 한 번에 한 달.

## 다른 안

| 안 | 버린 이유 |
|---|---|
| PythonOperator로 함수를 직접 부르기 | Airflow 프로세스 안에서 Spark를 띄우게 되고, 손으로 돌리는 경로와 코드가 갈린다 |
| Airflow 안에 Spark 설치 | 이미지가 커지고 Spark 버전이 두 곳에서 관리된다 |
| cron + 셸 스크립트 | 다시 시도 · 상태 · 백필 · 화면을 직접 만들어야 한다 |

## 치르는 값

- Docker 소켓을 Airflow 컨테이너에 연결한다(호스트의 Docker를 다룰 수 있음) — 이 PC 안 개발용으로만 쓰고, 화면도
  127.0.0.1에서만 연다.
- Bronze를 다른 디스크에 두면 Airflow를 켤 때 `MDP_BRONZE_ROOT`를 꼭 줘야 한다([0005](0005-manifest-as-state.md)의 치르는 값).

## 근거

- 다시 시도가 안전한 이유는 명령이 멱등이기 때문이다: DAG로 2024-03을 다시 돌려(14분 16초) Silver · Gold 7개 표가
  이전 버전과 **행 단위로 같았고**, PostgreSQL 복사본 SHA-256도 같았다.
