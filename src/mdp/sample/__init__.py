"""키 없는 샘플 시연 — 합성 종목 ``DEMOUSDT`` 로 파이프라인을 처음부터 끝까지 (README 6-1).

무엇: 바이낸스 아카이브와 같은 모양의 합성 Bronze(zip · 체크섬 목록)를 만들고
      (:mod:`~mdp.sample.generate`), 평소 명령으로 Silver → 검산 → Gold → PostgreSQL
      (→ 스트림 → 대사)을 돌린 뒤(:mod:`~mdp.sample.demo`),
      일부러 넣은 결함을 파이프라인이 정확히 찾았는지 DuckDB 로 확인한다(:mod:`~mdp.sample.verify`).
읽고 쓰는 것: ``data/sample/`` 아래만 (진짜 레이크 ``data/lake`` 는 건드리지 않는다).
"""
