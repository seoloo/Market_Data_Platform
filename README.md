# Market Data Platform

> 개인 퀀트 연구 저장소(「오르빗」)의 시장 데이터 코드를 **데이터 엔지니어링 포트폴리오**로 옮겨 가는 저장소.
> 원본 → 정제 → 연구용 계층(메달리온 레이크하우스), Spark 배치, Kafka 스트림, 데이터 품질 감사와 다시 처리하기를 보여 주는 것이 목표다.
> 큰 데이터는 Delta Lake 에, 실행 기록 · 감사 결과 · 조회용 표처럼 작고 자주 보는 것은 로컬 PostgreSQL 에 둔다.
> Delta 표는 Spark 가 쓰고, 가벼운 조회 · 검산은 DuckDB 가 Spark 없이 읽는다.

**지금 상태: 전환 중.** 아래 그림 중 1 · 3 은 지금 있는 것, 2 · 4 는 앞으로 만들 계획이다(그림 오른쪽 위 표시).

## 진행 순서

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 원본 연구 저장소를 히스토리째 복사 | ✅ 끝 |
| 1 | DE 코드(시장 데이터 · 저장 계층 · 체결 추출) 이름 풀기 · `[쓰임]`/`[이유]` 주석 — 동작 변경 없음 | ✅ 끝 · [기록](docs/DE_REFACTOR.md) |
| 2 | 실주문 · 실계좌 코드 걷어 내기 — 묶음 1/6 끝, 나머지는 파이프라인 뒤 **공개 전까지** (6-3) | 🟡 일부 · [목록](docs/LIVE_REMOVAL_PLAN.md) |
| 3-1 | **Bronze 받기** — 바이낸스 공개 아카이브를 받은 그대로 보관 + 체크섬 대조 · 체크섬 목록 | ✅ 끝 · BTCUSDT 2024-03 (체결 · 1 분봉 · funding) |
| 3-2 | 실행 기록 — 로컬 PostgreSQL(Docker Compose 프로필 `db`) | ✅ 끝 · 작업마다 코드 버전 · 인자 해시 · 산출물 체크섬 · 최대 메모리 |
| 3-3 | 증분 받기 · 백필 — 새 파일 · 빠진 날만, 두 번 돌려도 같은 결과 | ⏳ 다음 |
| 4-1 | Silver 감사 — Spark · Delta 로 중복 · 빈 곳 · 잘림, 걸러 낸 것은 격리 표로 | ⏳ 계획 |
| 4-2 | DuckDB 조회 층 — Silver Delta 표를 Spark 없이 SQL 로 읽고 감사 결과 검산 | ⏳ 계획 |
| 4-3 | 벤치마크 — 기존 스크립트 · Spark · DuckDB 로 같은 한 달치 (시간 · 최대 메모리 · 결과 일치) | ⏳ 계획 |
| 4-4 | Gold 1 분봉 — 체결로 만들고 PostgreSQL 조회용 표로 복사 | ⏳ 계획 |
| 4-5 | 재처리 시연 — 규칙 변경 → 다시 만들기 → Delta 시간여행으로 전후 비교 | ⏳ 계획 |
| 4-6 | Airflow 로 묶기 — 받기 → 감사 → Silver → Gold → 검산, 재시도 · 백필 | ⏳ 계획 |
| 5-1 | Kafka 스트림 1 분봉 | ⏳ 계획 |
| 5-2 | 매일 대사 — 스트림 봉 = 배치 봉, 결과는 PostgreSQL | ⏳ 계획 |
| 6-1 | 키 없는 샘플 시연 — 합성 데이터로 끝까지, 결과 확인은 DuckDB | ⏳ 계획 |
| 6-2 | 결정 기록(ADR) — 왜 Delta · 왜 Spark 와 DuckDB 둘 다 · 왜 PostgreSQL 은 작은 표만 | ⏳ 계획 |
| 6-3 | 실거래 코드 정리 나머지(묶음 2 ~ 6) · 화면 | ⏳ 계획 |

단계가 끝날 때마다 이 표와 그림을 함께 고친다. 그림 고치는 법은 [docs/diagrams](docs/diagrams/README.md).
처음에는 BTCUSDT 선물 한 달치로 받기부터 Gold 까지 끝까지 통과시킨 뒤 기간을 늘린다.

## 1. 지금 구조

연구마다 스크립트가 따로 받아 SQLite 한 파일에 넣는다. 잘 된 점(마감봉만 쓰기 · 체크섬)과 막힌 점(메모리 · 쓰기 잠금 · 다시 처리 불가 · 계층 없음)을 함께 적었다.

![그림 1 · 지금 구조](docs/diagrams/01_as_is.png)

## 2. 목표 구조

Bronze 에 받은 그대로 두고, Silver · Gold 는 언제든 다시 계산한다. 배치와 스트림이 같은 답을 내는지 매일 맞춰 본다.

PostgreSQL 은 로컬 Docker 한 대로, 작고 자주 보는 표만 둔다. 체결(약 64.6 억 행)까지 넣으면 색인을 빼고도 수백 GB 로
추정되어(재 보지 않은 추정) PC 의 SSD 에 들어가지 않기 때문이다. 조회용 1 분봉은 Gold 의 복사본이고 원본은 Delta 하나다.

![그림 2 · 목표 구조](docs/diagrams/02_to_be.png)

## 3. 체결 데이터 품질 감사 (이미 한 일)

바이낸스 선물 체결 기록을 연구에 쓰기 전에 결함부터 찾은 과정. 숫자는 [연구 216 문서](docs/research/216-quarter-hour-flow-pilot.md) §4.3 그대로다.

![그림 3 · 체결 데이터 품질 감사](docs/diagrams/03_trade_audit.png)

## 4. 스트림 · 배치 대사

PC 가 늘 켜져 있지 않아 스트림은 끊긴다. 목표는 '무중단' 이 아니라 빈 곳을 정확히 찾아 배치로 메우는 것이다.

![그림 4 · 스트림 · 배치 대사](docs/diagrams/04_reconcile.png)

## 돌려 보기

```bash
pip install -e ".[dev,lake]"
docker compose --profile db up -d                     # 실행 기록용 PostgreSQL (선택)
export MDP_PG_DSN=postgresql://mdp:mdp_local_only@127.0.0.1:56432/mdp
python -m mdp.bronze.fetch --dataset aggTrades --symbol BTCUSDT --from-month 2024-03 --probe
python -m mdp.bronze.fetch --dataset aggTrades --symbol BTCUSDT --from-month 2024-03
```

- `--probe` 는 받지 않고 크기만 본다. Bronze 위치는 `--root` 또는 `MDP_BRONZE_ROOT`, 없으면 `data/lake/bronze`.
- `MDP_PG_DSN` 이 없으면 실행 기록만 빼고 그대로 돈다.

## 저장소 안내

| 곳 | 내용 |
|---|---|
| `src/mdp/` | **새 파이프라인 코드** (README 3 단계부터). `auto_trading` 을 import 하지 않는다 |
| `src/mdp/bronze/` | Bronze 받기 — 원천 zip 을 흘려 받으며 체크섬 대조, 원천 경로 그대로 보관 · 덮어쓰지 않음 |
| `manifests/bronze/` | 받은 원천 파일의 체크섬 목록(경로 · 크기 · SHA-256) — 데이터 대신 이것만 커밋 |
| `src/mdp/runlog/` | 실행 기록 — 작업(`pipeline_runs`) · 산출물(`run_artifacts`)을 PostgreSQL 에, 마이그레이션 SQL |
| `docker-compose.yml` | 로컬 서비스, 필요한 프로필만 켠다 (지금은 `db` = PostgreSQL) |
| `src/auto_trading/marketdata/` | 바이낸스 공개 아카이브 받기 · 백필 · 묶어 쓰기 · 검사 · 리샘플 |
| `src/auto_trading/storage/` | SQLite 연결 · 마이그레이션 · 표별 저장소 |
| `scripts/run_qflow_216.py` | 체결(aggTrades) 추출 · 품질 감사 (그림 3) |
| [docs/DE_REFACTOR.md](docs/DE_REFACTOR.md) | DE 코드 리팩토링 범위 · 규칙 · 바뀐 이름 |
| [AGENTS.md](AGENTS.md) | 이 저장소 작업 규칙 (맨 앞 절대 규칙) |

- 원본 저장소에서 그대로 옮겨 온 연구 · 실거래 코드가 아직 남아 있다. 이 폴더에는 거래소 키 파일(`.env`)이 없어 접속할 수 없고,
  공개 전에 모두 걷어 낸다(6-3).
- **데이터 파일은 커밋하지 않는다.** 받는 코드와 체크섬 목록만 둔다. 바이낸스 등 원천 데이터의 재배포 조건은 **확인 필요**.
