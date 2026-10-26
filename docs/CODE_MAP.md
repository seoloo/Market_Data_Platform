# 코드 지도 (2026-10-26)

> 처음 보는 사람이 "무엇이 · 어디에 · 언제 도는지" 찾는 지도. 이 저장소는 **두 부분**이다.
>
> 1. **`src/mdp/` — 데이터 엔지니어링 파이프라인** (이 포트폴리오의 본체, README 1 ~ 10)
> 2. **`src/auto_trading/` · `scripts/` — 원본 퀀트 연구 저장소에서 옮겨 온 연구 코드** (가상 장부 Paper ·
>    백테스트 · 연구 판정). 실주문 · 실계좌 · 화면 · 예약 작업 코드는 걷어 냈다([README 6-3](../README.md),
>    [진행 기록](LIVE_REMOVAL_PLAN.md)).
>
> 파일을 열면 맨 위에 **언제 실행 · 읽고 쓰는 것**, 함수마다 `[쓰임]`(하는 일) · `[이유]`(왜 이렇게)가 있다.
> 예전(실거래 시절) 지도는 git 이력(`git log -- docs/CODE_MAP.md`)에 있다.

## 1. 데이터 엔지니어링 파이프라인 — `src/mdp/`

```text
국내 주식 (한국투자증권, 시세 조회만 · 주 원천) ─▶ kis 접속 창구 ─▶ bronze (kis · imported) ─▶ silver · gold (kr_stocks) ─▶ … K-4 부터 만드는 중
바이낸스 공개 아카이브 (두 번째 원천) ─▶ bronze ─▶ silver ─▶ gold ─▶ serve (PostgreSQL)
                                     │          │
                                     └─ query (DuckDB 검산 · SQL) · bench · reprocess
실시간 체결 / Bronze 재생 ─▶ stream (Kafka → Spark Structured Streaming) ─▶ reconcile (매일 대사)
Airflow DAG (airflow/dags) ─▶ orchestrate 도우미가 달 · Spark 컨테이너 경로를 정함
sample ─▶ 키 없는 합성 데이터로 위 전체를 한 번에 + 정답 맞추기
```

| 폴더 | 하는 일 | 어디서 도나 | 기록 |
|---|---|---|---|
| `kis/` | 국내 주식 원천 접속 창구 — 허용한 시세 조회만 · 토큰 캐시 · 요청 간격 · 다시 시도 · 이어 받기 | 호스트 · Airflow | [KIS](KIS.md) |
| `bronze/` | 받기 · 체크섬 대조 · 증분 받기와 백필 · 체크섬 목록 — 바이낸스 `fetch` · `sync`, 국내 주식 `kis`, 받아 둔 자료 `imported` | 호스트 · Airflow | [KIS](KIS.md) |
| `silver/` | 출처 고르기 · 정제 · 격리 · 빈 곳 · 날 대조 (바이낸스 `agg_trades`, 국내 주식 `kr_stocks` — 장 구간 · 빈 구간 · 감사) | Spark 컨테이너 | [SILVER_AGG_TRADES](SILVER_AGG_TRADES.md) · [KR_SILVER_GOLD](KR_SILVER_GOLD.md) |
| `gold/` | 체결 → 1분봉 (`bar_aggregations` — 스트림과 같은 집계식) · 공식 봉 대조, 국내 주식 `kr_stocks` — 분봉 ↔ 일봉 대조 · 수정주가 사건 | Spark 컨테이너 | [GOLD_BARS_1M](GOLD_BARS_1M.md) · [KR_SILVER_GOLD](KR_SILVER_GOLD.md) |
| `serve/` | Gold → PostgreSQL 한 트랜잭션 복사 · 양쪽 SHA-256 | 호스트 (DuckDB) | 〃 |
| `query/` | Delta 표를 Spark 없이 SQL로 · Silver 검산 | 호스트 (DuckDB) | README 4-2 |
| `bench/` | 같은 과제를 연구 추출기 · DuckDB · Spark로 | 호스트 + 컨테이너 | [BENCHMARK](BENCHMARK.md) |
| `reprocess/` | 규칙 바꿔 다시 만들기 · 버전 비교 · 되돌리기 | 호스트 + 컨테이너 | [REPROCESSING](REPROCESSING.md) |
| `orchestrate/` | Airflow가 돌릴 달 · Spark 컨테이너에 붙일 호스트 경로 | Airflow | [AIRFLOW](AIRFLOW.md) |
| `stream/` | 원문 토픽에 보내기(실시간 · 재생 + 결함 섞기) · 스트림 1분봉 · 배치와 맞대기 | Spark 컨테이너 · 호스트 | [STREAM](STREAM.md) |
| `reconcile/` | 매일 대사 — 원인 나누기 · 배치 봉으로 메우기 · PostgreSQL 기록 | Spark 컨테이너 | [RECONCILE](RECONCILE.md) |
| `sample/` | 합성 Bronze(결함 + 정답지) · 한 번에 돌리기 · 정답 맞추기 | 호스트 → 컨테이너 | [SAMPLE](SAMPLE.md) |
| `ui/` | 상태 화면 — 레이크 · 실행 기록 · 대사를 HTML 한 장으로 (읽기만) | 호스트 · Airflow | [STATUS_PAGE](STATUS_PAGE.md) |
| `runlog/` | 실행 기록(PostgreSQL) · 마이그레이션 SQL | 어디서나 | README 3-2 |
| `spark/` | Delta를 켠 로컬 Spark 세션 · 표 경로 · CSV 글자 그대로 읽기 | 컨테이너 | — |
| `verify/` | 기존 연구 추출기와 비교 | 호스트 | [발견 기록](findings/2026-10-21-qflow216-check-minute-chunk-boundary.md) |

실행 환경: [`docker-compose.yml`](../docker-compose.yml) 프로필 `db` · `spark` · `airflow` · `stream`,
이미지 [`docker/spark`](../docker/spark/Dockerfile) · [`docker/airflow`](../docker/airflow/Dockerfile),
DAG [`airflow/dags/mdp_monthly_pipeline.py`](../airflow/dags/mdp_monthly_pipeline.py). 시험은 `tests/mdp/`
(호스트) · `tests/mdp/spark/` (컨테이너). 왜 이렇게 짰는지는 [설계 결정](adr/README.md).

## 2. 옮겨 온 연구 코드 — `src/auto_trading/`

원본 저장소의 연구 · 가상 장부 코드다. **주문을 보낼 수 없다** — 거래소 연결은 키 없는 공개 시세
(`exchange/binance_spot.py` `BinanceSpotPublic` · `binance_usdm_public.py`)와 시험 · Paper용 가짜 거래소
(`fake_*`)뿐이다.

| 패키지 | 하는 일 |
|---|---|
| `core/` · `features/` · `strategy/` · `risk/` | 순수 계산 계층 (I/O 없음 — import-linter 계약으로 지킨다) |
| `backtest/` | 백테스트 엔진 · 비용 모델 |
| `research/` | 연구별 분석 코드 (엘리엇 · 옵션 보호 · 펀더멘털 등) |
| `marketdata/` · `storage/` | 시세 받기(공개) · SQLite 저장소 (Paper 장부 · 캔들) |
| `execution/` | Paper 실행기(가짜 거래소와 함께) · `futures_orders` (연구 시험이 씀, 정할 것 3) |
| `ops/` | Paper · 그림자 운영 계산 · 일정 · 뉴스 분류 · 정보 알림 계산 |
| `notify/` · `ai/` · `news/` · `macro/` | 알림 채널(텔레그램 · 파일) · AI 판단 기록 · 뉴스 · 거시 일정 |
| `config/` | 설정 스키마 · 로더 · `.env` 읽기(텔레그램 · AI 키만) |

## 3. 옮겨 온 스크립트 — `scripts/` (353 개)

| 무리 | 수 | 예 |
|---|---:|---|
| 연구 실행 `run_*` | 180 | 사전 등록한 연구를 한 번 돌려 `reports/` 에 결과 |
| 자료 감사 `audit_*` | 70 | 자료 결함 · 기간 · 누출 점검 |
| 자료 받기 `fetch_*` | 16 | 공개 원천 받기 (실계좌 키로 받던 것은 '받지 않음' 으로 멈춤) |
| Paper · 그림자 | 십여 개 | `lt_paper` · `nq_paper` · `qs_paper` · `run_paper_t2` · `run_shadow_t2` · `polaris_paper` — 저장된 시세 · 공개 시세로만 |
| 정보 알림 (정할 것 5, 실주문과 무관) | 몇 개 | `info_bot` · `market_brief` · `theme_alert` · `leader_alert` · `event_alert` |

연구 문서 · 판정 결과(`docs/research/` · `reports/`)의 숫자와 결론은 바꾸지 않는다(절대 규칙 6).
연구 판정 요약은 `docs/RESEARCH_SUMMARY_FOR_LLM.txt` (원본 저장소 기록). 원본 운영 기록(PROGRESS · 운영 사양)은
이 저장소에서 뺐다(2026-10-24).
