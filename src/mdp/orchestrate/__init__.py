"""오케스트레이션 도우미 — Airflow DAG(``airflow/dags``)가 쓰는, Airflow 없이도 시험할 수 있는 부분.

무엇: 실행할 달 정하기(:mod:`mdp.orchestrate.window`), Spark 컨테이너에 붙일 호스트 경로 찾기
      (:mod:`mdp.orchestrate.mounts`).
언제 쓰나: DAG 를 읽을 때 · 작업을 만들 때. 호스트 시험에서도 부른다.
읽고 쓰는 것: Docker 엔진(컨테이너 정보 읽기만, :mod:`mdp.orchestrate.mounts`).
"""
