"""조회용 복사 — Gold Delta 표를 로컬 PostgreSQL 로 옮겨, 앱 · 대시보드가 SQL 로 바로 읽게 한다.

무엇: 원본은 Delta 하나이고 PostgreSQL 표는 복사본이다. 복사한 뒤 양쪽의 행 수 · 체크섬을 맞춰 본다.
언제 쓰나: Gold 를 만든(다시 만든) 뒤. 호스트에서 돈다(DuckDB 가 Delta 를 읽어 PostgreSQL 로 쓴다).
읽고 쓰는 것: ``<lake>/gold/*`` (읽기) → PostgreSQL ``gold_*`` 표 · 실행 기록.
"""
