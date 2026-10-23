# Market Data Platform

바이낸스 선물 **체결 기록(aggTrades)**을 받아 원본 그대로 보관하고(Bronze), Spark · Delta Lake로
정제 · 품질 감사(Silver)하는 **단일 PC 레이크하우스 파이프라인**입니다. 모든 단계는 다시 돌려도 같은
결과가 나오고(멱등), 무엇을 어떤 코드로 만들었는지 PostgreSQL 실행 기록에 남습니다.

| 핵심 결과 (BTCUSDT 2024-03, 한 달치) | |
|---|---|
| 처리량 | 체결 **66,512,993행** (원본 zip 847MB · 압축 해제 4.4GB → Delta 1.8GB) |
| 정확성 | 기존 연구 스크립트의 전수 검산 결과와 **모든 항목 일치** (매시 00분 점검값 744/744) |
| 발견 | 같은 비교에서 기존 빠른 추출기의 **덩어리 경계 버그**를 찾아 원인까지 확인 ([기록](docs/findings/2026-10-21-qflow216-check-minute-chunk-boundary.md)) |
| 재현성 | 같은 달을 다시 만들어 Delta 이전 버전과 행 단위로 비교 — **차이 0행** |
| 성능 | Silver 한 달치 **65분 → 18분** (Spark 지연 실행 때문에 반복되던 계산을 한 번으로) |

> 개인 퀀트 연구에서 쓰던 시장 데이터 수집 · 감사 스크립트를 데이터 엔지니어링 방식으로 다시 만드는
> 프로젝트입니다. 지금은 Bronze · Silver까지 구현했고, DuckDB · Gold · Airflow · Kafka가 다음 차례입니다
> ([로드맵](docs/ROADMAP.md)).

## 1. 지금 구현된 흐름

```mermaid
flowchart LR
    SRC["바이낸스 공개 아카이브<br/>월 · 일 zip + CHECKSUM"] -->|흘려 받기 · SHA-256 대조| BRZ
    subgraph BRZ["Bronze — 받은 그대로"]
        B1["zip 원본 (원천 경로 그대로)<br/>덮어쓰지 않음"]
        B2["체크섬 목록 CSV<br/>(저장소에 커밋)"]
    end
    BRZ -->|읽기 직전 재대조| SLV
    subgraph SLV["Silver — Spark 4.0 + Delta 4.0 (Docker)"]
        S1["agg_trades<br/>깨끗한 체결"]
        S2["agg_trades_quarantine<br/>못 쓰는 줄 + 이유"]
        S3["audit_file · gaps · audit_daily<br/>결함 집계 · 위치 · 1분봉 대조"]
    end
    BRZ -. 작업마다 .-> LOG[("PostgreSQL 실행 기록<br/>코드 버전 · 인자 해시 ·<br/>산출물 체크섬 · Delta 버전")]
    SLV -. 작업마다 .-> LOG
```

목표 구조 전체(Gold · DuckDB · Kafka 스트림 · 매일 대사 포함)는 아래 그림입니다. 오른쪽 위에 '계획'으로
표시돼 있고, 단계가 끝날 때마다 고칩니다.

![목표 구조](docs/diagrams/02_to_be.png)

## 2. 왜 만들었나

연구용 스크립트는 연구마다 따로 데이터를 받아 SQLite 파일 하나에 넣었습니다. 그 방식의 한계입니다.

| 문제 | 실제로 겪은 일 | 이 프로젝트에서 |
|---|---|---|
| 메모리 | 작업 1,776개를 한 번에 제출해 메모리가 6.7GB까지 치솟음 | 1MiB씩 흘려 받기 — 847MB 파일 대조 중 최대 58MiB |
| 쓰기 충돌 | 수집기 두 개가 같은 SQLite에 쓰면 뒤의 것이 한 건도 못 씀 | 큰 데이터는 파일(Delta), 작은 기록만 PostgreSQL |
| 다시 처리 불가 | 체결 원본 86GB를 집계 뒤 지워, 규칙을 바꾸려면 처음부터 다시 받아야 함 | Bronze에 원본을 남기고 Silver는 언제든 다시 계산 |
| 이력 없음 | 원본 · 정제 · 분석 구분, 파티션, 실행 기록이 없음 | 계층 분리 · 날짜 파티션 · 실행 기록 · Delta 버전 |

## 3. 데이터 모델

**Bronze** — 원천 주소의 경로를 그대로 씁니다. 파일만 보고도 어디서 왔는지, 다시 받을 주소가 무엇인지 알 수 있습니다.

```text
data/futures/um/monthly/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-03.zip   (+ .CHECKSUM)
data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-03-05.zip  (백필한 날)
data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-03.zip
```

**Silver** (Delta, `<lake>/silver/`) — 한 달치를 만들 때 그 달 칸만 바꿔 끼웁니다(`replaceWhere`).

| 표 | 한 행 | 파티션 | 주요 열 |
|---|---|---|---|
| `agg_trades` | 체결 한 건 | `symbol`, `trade_date` | `agg_trade_id`, `price`·`quantity` (decimal 18,8), `transact_time_ms`, `is_buyer_maker`, `source_path`, `run_id` |
| `agg_trades_quarantine` | 못 쓰는 줄 하나 | `symbol`, `period_month` | `reason`(parse_error · outside_period · conflicting_duplicate), `raw_line` |
| `agg_trades_audit_file` | 원본 파일 하나 | `symbol`, `period_month` | 행 수, 중복, 빈 곳 수 · 빠진 번호 수, 시각 역행, 시각 단위, 일 파일에 넘긴 행 |
| `agg_trades_gaps` | 빈 곳 · 역행 한 곳 | `symbol`, `period_month` | 앞 · 뒤 번호와 시각, 빠진 번호 수 |
| `agg_trades_audit_daily` | 하루 | `symbol`, `trade_date` | 거래량 · 공격 매수량, 공식 1분봉 합계, 차이(%), `suspect` |

**실행 기록** (PostgreSQL) — `pipeline_runs`(작업 한 번: 상태 · 코드 버전 · 인자 해시 · 최대 메모리 · 오류),
`run_artifacts`(작업이 만든 파일 · 표: 경로 · 크기 · SHA-256 · 행 수 · Delta 버전). 표 정의는 번호 붙은
마이그레이션 SQL로 관리하고, 이미 적용한 파일이 바뀌면 멈춥니다.

## 4. 데이터 품질 규칙

| 단계 | 규칙 |
|---|---|
| 받기 | 원천 `.CHECKSUM`과 SHA-256이 맞을 때만 Bronze에 넣고, 이미 있는 파일이 다르면 덮어쓰지 않고 멈춤 |
| 출처 | 그 달 월 파일이 기본, Bronze에 일 파일을 받아 둔 날은 일 파일이 이김(잘린 날 교체용) |
| 읽기 | 모든 칸을 글자로 읽고 `try_cast` — 한 줄이 틀려도 작업이 멈추지 않고 그 줄은 격리 |
| 타입 | 가격 · 수량은 decimal(float 합은 더하는 순서에 따라 값이 달라짐), 시각은 UTC ms(마이크로초 표기 보정) |
| 격리 | 못 읽은 줄 · 파일 기간 밖 줄 · 같은 번호인데 내용이 다른 줄 |
| 중복 | 내용까지 같은 중복은 하나만 남기고 개수를 감사 표에 기록 |
| 빈 곳 · 역행 | 체결 번호 순으로 이웃을 비교. 번호를 100만 단위로 나눠 여러 스레드가 정렬하고 경계만 따로 봄 |
| 날 대조 | 그날 거래량을 공식 1분봉 합계와 비교해 0.5% 넘게 다르면 의심일 |

자세한 규칙과 결과는 [docs/SILVER_AGG_TRADES.md](docs/SILVER_AGG_TRADES.md)에 있습니다.

## 5. 어떻게 검증했나

| 검증 | 방법 | 결과 |
|---|---|---|
| 기존 결과 재현 | 같은 월 파일을 기존 연구 스크립트의 추출기로도 처리해 항목별 비교 (`mdp.verify.compare_216`) | 전수 검산 추출과 행 수 · 번호 · 시각 · 빈 곳 · 역행 · 점검값 744개 모두 일치 (float 끝자리 차이만, 상대 오차 1.2e-13) |
| 버그 발견 | 위 비교에서 기존 **빠른** 추출기와만 1분이 다름 | 32MB 덩어리 경계가 점검 분 중간(09:00:14)에 걸리면 나머지를 건너뛰는 조건 오류 — 해당 분 8,490건 중 2,630건만 셈 |
| 최적화 전후 | Delta 시간여행으로 느린 판(v0)과 빠른 판(v1)을 `exceptAll` 양방향 비교 | 66,512,993행 차이 0 |
| 날 대조 | 31일 거래량을 공식 1분봉 합계와 비교 | 모두 통과, 최대 차이 0.00046% |
| 결함 주입 | 머리줄 · 깨진 줄 · 기간 밖 · 빈 곳 · 두 종류 중복 · 역행 · 마이크로초 시각을 넣은 작은 CSV | Spark 시험 7개 통과 |
| 멱등 | 같은 받기 · 같은 달 Silver를 두 번 | 두 번째는 받지 않고 건너뜀, 체크섬 목록 변경 0, Silver는 같은 내용의 새 버전 |

기존 연구 문서와 스크립트는 고치지 않았습니다. 버그와 그 영향(확인 필요)은 [발견 기록](docs/findings/2026-10-21-qflow216-check-minute-chunk-boundary.md)에 따로 남겼습니다.

## 6. 성능과 한계

| Silver 한 달치 | 시간 | 차이 |
|---|---:|---|
| 첫 판 | 65분 | 표 5개 · 행 수 세기 · 보고서마다 CSV 읽기부터 다시 계산 |
| 최적화 | **18분** | 형식을 맞춘 줄과 깨끗한 체결을 임시 Parquet으로 한 번만 만들어 재사용 |

환경: 단일 PC(RAM 32GB, 6코어 12스레드), Docker 안 Spark `local[8]`, 드라이버 메모리 6GB.

- 아직 **한 달치**(BTCUSDT 2024-03)만 끝까지 돌렸습니다. 기간을 늘리는 것은 로드맵에 있습니다.
- 실행 기록의 '최대 메모리'는 파이썬 프로세스만 잰 값입니다. Spark 자바 프로세스는 `docker stats`로 약 9.5GB까지 썼습니다. 벤치마크 단계에서 컨테이너 메모리로 다시 잽니다.
- 기존 스크립트와의 시간 비교는 하는 일이 달라(값 몇 개만 뽑기 vs 전체 표 만들기) 아직 공정한 비교가 아닙니다. 같은 조건의 벤치마크는 다음 단계입니다.

## 7. 설계 결정

| 결정 | 이유 |
|---|---|
| 큰 데이터는 Delta, PostgreSQL은 작은 표만 | 체결 64.6억 행을 PostgreSQL에 넣으면 색인 없이도 수백 GB로 추정(재 보지 않은 추정) — SSD 여유보다 큼 |
| Spark를 Docker 안에서 | Windows에서 Spark 파일 쓰기에 필요한 winutils 문제를 피하고 어느 PC에서나 같은 환경 |
| '이미 받았나'는 체크섬 목록으로 판단 | 실행 기록 DB 없이도 같은 결과 — 목록은 저장소에 커밋돼 기준이 하나 |
| 달 단위 `replaceWhere` | 같은 달을 다시 만들면 그 달만 바뀜(멱등), 이전 내용은 Delta 이력에 남음 |
| decimal | 합계가 더하는 순서와 상관없이 같아야 검증이 가능 |

## 8. 돌려 보기

준비물: Python 3.11 이상, Docker Desktop, 디스크 약 1GB(한 달치 Bronze). 처음 받기는 약 850MB를 내려받습니다.

```bash
pip install -e ".[dev,lake]"
docker compose --profile db up -d        # 실행 기록용 PostgreSQL (선택)
docker compose --profile spark build     # Spark + Delta 이미지 (한 번)
```

환경 변수 (Windows PowerShell은 `$env:이름 = "값"`, bash는 `export 이름=값`):

| 이름 | 뜻 | 없으면 |
|---|---|---|
| `MDP_BRONZE_ROOT` | Bronze 위치 (큰 원본이라 별도 디스크 권장) | `data/lake/bronze` |
| `MDP_LAKE_ROOT` | Silver · Gold Delta 표 위치 | `data/lake` |
| `MDP_PG_DSN` | 실행 기록 PostgreSQL (예: `postgresql://mdp:mdp_local_only@127.0.0.1:56432/mdp`) | 기록 없이 실행 |

```bash
python -m mdp.bronze.fetch --dataset aggTrades --symbol BTCUSDT --from-month 2024-03 --probe   # 크기만 보기
python -m mdp.bronze.sync  --dataset aggTrades --symbol BTCUSDT --start 2024-03-01 --end 2024-03-31
python -m mdp.bronze.sync  --dataset klines --interval 1m --symbol BTCUSDT --start 2024-03-01 --end 2024-03-31
docker compose run --rm spark python -m mdp.silver.agg_trades --symbol BTCUSDT --month 2024-03
python -m mdp.verify.compare_216 --symbol BTCUSDT --month 2024-03                            # 기존 추출기와 비교
```

시험:

```bash
pytest tests/mdp                                                   # 파이프라인 시험 44개 (PostgreSQL 시험 3개는 MDP_TEST_PG_DSN 있을 때)
docker compose run --rm spark python -m pytest tests/mdp/spark -q  # Spark 시험 7개 (컨테이너 안)
ruff check src tests && mypy
```

## 9. 기술 스택 · 구조

| 구분 | 사용 |
|---|---|
| 언어 · 품질 | Python 3.11+ (Spark 이미지 안은 3.12), pytest, mypy(strict), ruff |
| 처리 | Apache Spark 4.0.1, Delta Lake 4.0.1 |
| 저장 | Delta(파일 레이크하우스), PostgreSQL 17 (실행 기록) |
| 실행 환경 | Docker Compose 프로필 (`db`, `spark`) |
| 다음 단계 | DuckDB, Apache Airflow, Apache Kafka |

```text
src/mdp/
├─ bronze/    받기(fetch) · 증분 받기와 백필(sync) · 원천 파일 이름표
├─ silver/    출처 고르기 · 검증 · 풀기(sources) · Silver 체결 감사(agg_trades)
├─ spark/     Delta를 켠 로컬 Spark 세션 · 표 경로
├─ runlog/    실행 기록(PostgreSQL) · 마이그레이션 SQL
└─ verify/    기존 연구 추출기와 비교
manifests/bronze/   받은 원천 파일의 체크섬 목록 (데이터 대신 커밋)
docker/spark/       Spark + Delta 이미지
docs/               규칙 · 결과 · 발견 기록 · 로드맵 · 그림 원본
```

## 10. 로드맵

| 상태 | 단계 |
|---|---|
| ✅ 끝 | Bronze 받기 · 실행 기록 · 증분 받기와 백필 · Silver 체결 감사 |
| ⏳ 다음 | DuckDB 조회 층 — Silver Delta 표를 Spark 없이 SQL로 검산 |
| ⏳ 이후 | 벤치마크(기존 스크립트 · Spark · DuckDB) → Gold 1분봉 → 재처리 시연(시간여행 비교) → Airflow → Kafka 스트림 + 매일 대사 → 키 없는 샘플 시연 · 설계 결정 기록 |

단계별 전체 기록은 [docs/ROADMAP.md](docs/ROADMAP.md)에 있습니다.

## 데이터와 재배포

- **데이터 파일은 커밋하지 않습니다.** 받는 코드와 체크섬 목록만 둡니다.
- 바이낸스 공개 아카이브(data.binance.vision) 데이터의 재배포 조건은 **확인 필요**입니다. 확인 전까지 받은 데이터와 그 가공물을 공개하지 않습니다.
