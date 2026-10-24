"""파이프라인 상태 화면 — 레이크(Delta) · 실행 기록 · 대사 결과를 한 장의 HTML 로 (README 6-3).

무엇: 읽기(:mod:`mdp.ui.data`) → 그리기(:mod:`mdp.ui.render`) →
      파일 쓰기 · 보기(:mod:`mdp.ui.report`).
읽고 쓰는 것: Delta 표(DuckDB, 읽기만) · PostgreSQL(읽기만) · HTML 파일 하나.

[이유] dbt docs · Great Expectations 'Data Docs' 처럼 **정적 페이지**로 만든다 — 서버 · 계정
       없이 열리고, 파이프라인이 끝날 때마다(Airflow 마지막 작업 · 샘플 시연 끝) 다시 만들면
       된다. 새 의존성도 없다.
"""
