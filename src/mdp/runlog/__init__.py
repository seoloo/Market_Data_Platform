"""실행 기록 — 작업마다 무엇을 · 어떤 코드로 · 무엇을 만들었나를 로컬 PostgreSQL 에 남긴다.

무엇: 작업 한 번(``pipeline_runs``)과 그 작업이 만든 · 확인한 파일(``run_artifacts``)을 기록한다.
언제 쓰나: 모든 파이프라인 명령(Bronze 받기부터)이 시작 · 끝에 부른다. DB 주소가 없으면 기록 없이
           돈다.
읽고 쓰는 것: PostgreSQL(``MDP_PG_DSN``, 기본 ``docker compose --profile db`` 의 127.0.0.1:56432) ·
              마이그레이션 SQL(``mdp/runlog/sql/*.sql``).
"""
