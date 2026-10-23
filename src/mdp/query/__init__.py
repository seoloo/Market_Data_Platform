"""조회 층 — DuckDB 로 Silver · Gold Delta 표를 Spark 없이 SQL 로 읽는다 (README 4-2).

무엇: Delta 표를 DuckDB 뷰로 등록하고(:mod:`mdp.query.duck`), Spark 가 쓴 감사 결과를 SQL 로 다시
      계산해 맞대 보며(:mod:`mdp.query.check_silver`), 아무 SQL 이나 바로 돌린다
      (:mod:`mdp.query.sql`).
언제 쓰나: Silver 를 만든 뒤 검산할 때, 분석 · 시연 때. 호스트에서 돈다(Docker · Spark 없음).
읽고 쓰는 것: ``<lake>/silver/*`` Delta 표(읽기만) · 실행 기록(검산 결과, ``MDP_PG_DSN`` 이 있을
              때).

[이유] 쓰기(표 만들기)는 Spark, 읽기 · 검산은 DuckDB 로 나눴다. 한 달치 6,651 만 행을 세는 데 Spark
       는 컨테이너를 띄우는 것만 수십 초지만 DuckDB 는 1 초 안쪽이다. 또 Spark 와 **다른 엔진**으로
       같은 값을 다시 계산해야 '엔진의 실수' 까지 걸러진다.
"""
