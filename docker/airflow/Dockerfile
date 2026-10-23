# Airflow 실행 이미지 (README 4-6). docker-compose.yml 프로필 airflow 가 만든다.
#
# 공식 Airflow 3.1.7 (Python 3.12) 에 이 저장소의 가벼운 단계(받기 · DuckDB 검산 · PostgreSQL 복사)가 쓰는
# 패키지만 더한다. 무거운 Spark 단계는 이 이미지에서 돌리지 않고, DAG 가 Spark 이미지(docker/spark)를 별도
# 컨테이너로 띄운다(DockerOperator) — 평소와 같은 이미지 · 같은 명령.
# Docker 연동(apache-airflow-providers-docker 4.5.2)은 공식 이미지에 이미 들어 있다(2026-10-24 확인).

FROM apache/airflow:3.1.7-python3.12

RUN pip install --no-cache-dir "psycopg[binary]==3.2.10" "duckdb==2.0.0"

# 우리 코드(/opt/mdp/src)는 compose 가 연결한다.
ENV PYTHONPATH=/opt/mdp/src
