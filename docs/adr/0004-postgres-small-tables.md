# 0004 — PostgreSQL에는 작고 자주 보는 표만 (로컬 Docker)

- 상태: 채택 · 2026-10-21
- 관련: [Gold 복사](../GOLD_BARS_1M.md) · [대사](../RECONCILE.md) · [마이그레이션](../../src/mdp/runlog/sql)

## 맥락

처음 실행 기록은 SQLite였다. 실무에서 흔한 PostgreSQL로 바꾸면서, '그럼 데이터도 전부 PostgreSQL에 넣을까'를 정해야
했다.

## 결정

- **큰 데이터(체결 · Silver · Gold 원본)는 Delta 파일**에 둔다. Delta가 원본이다.
- **PostgreSQL 17 (Docker, 이 PC에서만 열림)** 에는 작고 자주 보는 것만:

| 표 | 크기 감 |
|---|---|
| `pipeline_runs` · `run_artifacts` — 실행 기록(코드 버전 · 인자 해시 · 산출물 체크섬 · Delta 버전) | 작업마다 몇 줄 |
| `gold_bars_1m` — Gold 1분봉의 **조회용 복사본** | 한 달 44,640행 |
| `reconcile_daily` · `reconcile_minutes` — 대사 결과 (더하기만) | 하루 1줄 + 다른 분 |

- 복사본은 복사할 때마다 **양쪽 SHA-256**(Delta 쪽은 DuckDB, PostgreSQL 쪽은 자체 SQL)으로 같은지 증명한다.
- Airflow 자체 기록도 같은 PostgreSQL의 별도 DB `airflow`에 둔다(우리 기록과 섞지 않음).

## 다른 안

| 안 | 버린 이유 |
|---|---|
| 체결까지 PostgreSQL에 | 전체 기간 체결 약 64.6억 행 — 행 저장소로 색인 없이도 수백 GB로 **추정**(재 보지 않음). C: SSD 여유보다 크다 |
| 관리형 PostgreSQL (예: Supabase) | 외부 계정 · 네트워크가 필요해 키 없이 누구나 돌리는 시연(6-1)과 맞지 않는다. 작은 표만 두므로 로컬로 충분하다 |
| SQLite 유지 | 동시 쓰기(Airflow 작업 여럿) · 서버형 접속 · 실무 도구와의 연결에서 PostgreSQL이 낫다 |

## 치르는 값

- 같은 데이터가 두 곳(Delta · PostgreSQL)에 있다 → 원본은 하나(Delta)로 정하고 복사본은 체크섬으로 맞춘다.
- 서비스가 하나 는다(Compose 프로필 `db`). 마이그레이션은 체크섬으로 지켜, 이미 적용된 파일을 고치면 멈춘다.

## 근거

- Gold 복사(4-4): 44,640행 2.4초, 양쪽 SHA-256 같음. Airflow 재실행(4-6)에서도 같은 체크섬.
- 대사(5-2): 체크 제약 `matched + repaired + 원인들 = 배치 분 수`를 DB가 지킨다.
