# Market Data Platform

바이낸스 선물 **체결 기록(aggTrades)**을 받아 원본 그대로 보관하고(Bronze), Spark · Delta Lake로
정제 · 품질 감사(Silver)한 뒤 1분봉(Gold)을 만들어 PostgreSQL로 내보내는 **단일 PC 레이크하우스 파이프라인**입니다. 모든 단계는 다시 돌려도 같은
결과가 나오고(멱등), 무엇을 어떤 코드로 만들었는지 PostgreSQL 실행 기록에 남습니다.

| 핵심 결과 (BTCUSDT 2024-03, 한 달치) | |
|---|---|
| 처리량 | 체결 **66,512,993행** (원본 zip 847MB · 압축 해제 4.4GB → Delta 1.8GB) |
| 정확성 | 기존 연구 스크립트의 전수 검산 결과와 **모든 항목 일치** (매시 00분 점검값 744/744) |
| 발견 | 같은 비교에서 기존 빠른 추출기의 **덩어리 경계 버그**를 찾아 원인까지 확인 ([기록](docs/findings/2026-10-21-qflow216-check-minute-chunk-boundary.md)) |
| 재현성 | 같은 달을 다시 만들어 Delta 이전 버전과 행 단위로 비교 — **차이 0행** |
| 재처리 | 감사 규칙을 바꿔 다시 만들자 **한 표 한 칸 10행만** 바뀐 것을 시간여행으로 증명, `RESTORE`로 되돌린 뒤 7개 표 모두 원래와 같음 ([기록](docs/REPROCESSING.md)) |
| 교차 검산 | Spark가 쓴 날 감사 31일을 DuckDB로 처음부터 다시 계산 — **소수점까지 일치**, 6,651만 행 검산 9.5초 |
| 연구 재현 | Gold 1분봉이 연구 문서의 "2024-03-01, 1,440분 중 1,110분 일치 · 하루 거래량 차 −3.5e-6"을 **같은 숫자로** 다시 냄 ([기록](docs/GOLD_BARS_1M.md)) |
| 성능 | Silver 한 달치 **65분 → 18분** (Spark 지연 실행 때문에 반복되던 계산을 한 번으로) |
| 벤치마크 | 같은 과제를 4가지로 — **DuckDB 12초** · Spark 105초 · 기존 전수 추출 134초, 셋 다 같은 답 ([기록](docs/BENCHMARK.md)) |
| 오케스트레이션 | Airflow DAG 하나로 받기 → Silver → 검산 → Gold → 복사 **14분**, 다시 만든 7개 표가 이전과 행 단위로 같음 ([기록](docs/AIRFLOW.md)) |
| 스트림 | Kafka → Spark Structured Streaming 1분봉이 중복 · 순서 뒤바뀜을 섞은 하루 재생에서 배치 봉과 **1,440분 모두 같음**, 늦은 체결로 바뀐 26분을 스트림 혼자 찾아냄 ([기록](docs/STREAM.md)) |
| 매일 대사 | 스트림 봉 ↔ 배치 봉을 분마다 맞대 원인을 나눔 — 받지 못한 체결 **13,897 = 재생이 건너뛴 13,897**, 늦어서 빠진 **678 = Spark가 버린 678**, 배치 봉으로 메운 뒤 1,440분 모두 같음 ([기록](docs/RECONCILE.md)) |
| 키 없는 시연 | `python -m mdp.sample.demo --with-stream` 하나로 합성 데이터 → Silver → Gold → PostgreSQL → Kafka → 대사, 넣은 결함 5종을 **정답지와 12/12 맞춤, 107초** ([기록](docs/SAMPLE.md)) |

> 개인 퀀트 연구에서 쓰던 시장 데이터 수집 · 감사 스크립트를 데이터 엔지니어링 방식으로 다시 만드는
> 프로젝트입니다. 지금은 Bronze · Silver · Gold, DuckDB 조회 층, PostgreSQL 내보내기, 재처리, Airflow 오케스트레이션, Kafka 스트림 1분봉, 스트림 · 배치 매일 대사, 키 없는 샘플 시연까지 구현했고, 설계 결정 기록(ADR)이 다음 차례입니다
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
    SLV -->|Spark| GLD["Gold — bars_1m<br/>체결로 만든 1분봉 · 공식 봉 대조"]
    GLD -->|DuckDB 한 트랜잭션 · 체크섬 증명| PGB[("PostgreSQL gold_bars_1m<br/>조회용 복사본")]
    SLV -->|Spark 없이 SQL · 시간여행| DUCK["DuckDB 조회 층<br/>교차 검산 · 분석"]
    BRZ -. 작업마다 .-> LOG[("PostgreSQL 실행 기록<br/>코드 버전 · 인자 해시 ·<br/>산출물 체크섬 · Delta 버전")]
    SLV -. 작업마다 .-> LOG
    DUCK -. 검산 결과 .-> LOG
    AF{{"Airflow DAG (매달)<br/>순서 · 다시 시도 · 백필"}} -. 차례로 부름 .-> BRZ
    WS["바이낸스 실시간 체결<br/>또는 Bronze 재생"] -->|받은 그대로| KFK[("Kafka 원문 토픽<br/>보관 7일")]
    KFK -->|"워터마크 · 중복 제거 ·<br/>배치와 같은 집계식"| SB["Gold — stream_bars_1m<br/>확정된 분만 · MERGE"]
    SB -->|"매일 대사<br/>원인 나누기 · 메우기"| REC[("PostgreSQL<br/>reconcile_daily · minutes")]
    GLD -->|정답| REC
```

Airflow는 위 단계를 **평소와 같은 명령**으로 차례대로 부르고, 무거운 Spark 단계는 Spark 컨테이너를 따로 띄워
돌린다([기록](docs/AIRFLOW.md)).

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

**Gold** (Delta, `<lake>/gold/`) — Silver에서만 만든다. 줄마다 읽은 Silver 버전(`silver_version`)을 남긴다.

| 표 | 한 행 | 파티션 | 주요 열 |
|---|---|---|---|
| `bars_1m` | 1분 | `symbol`, `trade_date` | 시가 · 고가 · 저가 · 종가(첫 · 마지막 체결, 같은 밀리초면 번호 순), 거래량 · 거래대금 · 공격 매수량, `trade_count`(개별 체결 수) |
| `bars_1m_kline_check` | 하루 | `symbol`, `trade_date` | 공식 1분봉과 칸마다 같은 분 수, 하루 합계 비교 |

PostgreSQL `gold_bars_1m`은 `bars_1m`의 조회용 복사본입니다(달 단위로 지우고 다시 넣음, 복사한 Delta 버전 · 작업 번호 기록).

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
| 교차 검산 | Spark가 쓴 표를 DuckDB SQL로 다시 계산 (`mdp.query.check_silver`) — 불변 조건 5개(번호 중복 · 빈 칸 · 날짜 · 가격/수량 · 번호 범위) + 날 감사 재계산 | 66,512,993행 위반 0, 31일 감사 값 소수점까지 일치 (9.5초) |
| 기존 결과 재현 | 같은 월 파일을 기존 연구 스크립트의 추출기로도 처리해 항목별 비교 (`mdp.verify.compare_216`) | 전수 검산 추출과 행 수 · 번호 · 시각 · 빈 곳 · 역행 · 점검값 744개 모두 일치 (float 끝자리 차이만, 상대 오차 1.2e-13) |
| 버그 발견 | 위 비교에서 기존 **빠른** 추출기와만 1분이 다름 | 32MB 덩어리 경계가 점검 분 중간(09:00:14)에 걸리면 나머지를 건너뛰는 조건 오류 — 해당 분 8,490건 중 2,630건만 셈 |
| 최적화 전후 | Delta 시간여행으로 느린 판(v0)과 빠른 판(v1)을 `exceptAll` 양방향 비교 | 66,512,993행 차이 0 |
| 날 대조 | 31일 거래량을 공식 1분봉 합계와 비교 | 모두 통과, 최대 차이 0.00046% |
| 재처리 영향 범위 | 의심일 기준 0.5% → 0.0001%로 다시 만들고 7개 표의 이전 · 새 버전을 키로 맞대기 (`mdp.reprocess`) | 바뀐 것은 날 감사 10행의 `suspect` 칸뿐, 체결 · 봉은 0행 변경. `RESTORE` 뒤 모두 원래와 같음 |
| 복사본 증명 | Gold Delta → PostgreSQL 복사 후 같은 행 · 같은 순서로 이어 붙인 SHA-256을 양쪽에서 따로 계산 | 44,640행 같음, 다시 복사해도 한 벌 |
| 결함 주입 | 머리줄 · 깨진 줄 · 기간 밖 · 빈 곳 · 두 종류 중복 · 역행 · 마이크로초 시각을 넣은 작은 CSV | Spark 시험 7개 통과 |
| 세 엔진 교차 | 같은 월 파일을 기존 전수 추출 · DuckDB · Spark로 따로 계산 (벤치마크) | 세 엔진 모두 같은 답, 기존 빠른 추출만 버그 1분 |
| 멱등 | 같은 받기 · 같은 달 Silver를 두 번 | 두 번째는 받지 않고 건너뜀, 체크섬 목록 변경 0, Silver는 같은 내용의 새 버전 |

기존 연구 문서와 스크립트는 고치지 않았습니다. 버그와 그 영향(확인 필요)은 [발견 기록](docs/findings/2026-10-21-qflow216-check-minute-chunk-boundary.md)에 따로 남겼습니다.

## 6. 성능과 한계

| Silver 한 달치 | 시간 | 차이 |
|---|---:|---|
| 첫 판 | 65분 | 표 5개 · 행 수 세기 · 보고서마다 CSV 읽기부터 다시 계산 |
| 최적화 | **18분** | 형식을 맞춘 줄과 깨끗한 체결을 임시 Parquet으로 한 번만 만들어 재사용 |

**같은 과제 벤치마크** — 월 파일 하나(66,512,993행)에서 연구 추출기가 뽑던 값(요약 · 빈 곳 · 역행 · 점검 분 744개 · 10초 창 8,928개)을 네 방법으로 ([기록](docs/BENCHMARK.md)):

| 방법 | 걸린 시간 (2판) | 최대 메모리 | 정답지와 |
|---|---:|---:|---|
| 기존 전수 추출 (정답지) | 134초 · 145초 | 30MiB | — |
| 기존 빠른 추출 (연구에서 쓴 것) | 17초 · 17초 | 214MiB | ❌ 1분 다름 (버그) |
| **DuckDB** | **12초 · 12초** | 4.4GB | ✅ |
| Spark (Docker) | 105초 · 130초 | 8.2GB (컨테이너, 파일 캐시 포함) | ✅ |

이 크기에서는 DuckDB가 가장 빠르고, 기존 전수 추출은 느리지만 메모리를 거의 쓰지 않습니다. 그래서 **표 쓰기는 Spark + Delta**(트랜잭션 · 달 단위 바꿔 끼우기 · 기간이 늘면 메모리보다 큰 일), **읽기 · 검산은 DuckDB**로 나눴습니다.
DuckDB는 Windows에서 직접 읽고 Spark는 Docker가 연결한 폴더로 읽어 조건이 완전히 같지는 않습니다(차이 크기는 확인 필요).

환경: 단일 PC(RAM 32GB, 6코어 12스레드), Docker 안 Spark `local[8]`, 드라이버 메모리 6GB.

- 아직 **한 달치**(BTCUSDT 2024-03)만 끝까지 돌렸습니다. 기간을 늘리는 것은 로드맵에 있습니다.
- Silver 실행 기록의 '최대 메모리'는 파이썬 프로세스만 잰 값입니다. Spark 쪽 메모리는 위 벤치마크처럼 컨테이너 단위로 잽니다.

## 7. 설계 결정

| 결정 | 이유 |
|---|---|
| 큰 데이터는 Delta, PostgreSQL은 작은 표만 | 체결 64.6억 행을 PostgreSQL에 넣으면 색인 없이도 수백 GB로 추정(재 보지 않은 추정) — SSD 여유보다 큼 |
| Spark를 Docker 안에서 | Windows에서 Spark 파일 쓰기에 필요한 winutils 문제를 피하고 어느 PC에서나 같은 환경 |
| '이미 받았나'는 체크섬 목록으로 판단 | 실행 기록 DB 없이도 같은 결과 — 목록은 저장소에 커밋돼 기준이 하나 |
| 달 단위 `replaceWhere` | 같은 달을 다시 만들면 그 달만 바뀜(멱등), 이전 내용은 Delta 이력에 남음 |
| 스트림 봉과 배치 봉은 같은 집계 함수 | 두 봉이 다르면 원인이 '계산 방식'이 아니라 입력(빠짐 · 늦음 · 중복)에 있다고 좁힐 수 있음 |
| 대사 원인은 원문 토픽으로 가름 | 빠진 체결 번호가 원문 토픽에 있으면 '늦음', 없으면 '끊김' — 원인마다 할 일이 다름. 결과 표는 더하기만 해 메우기 전 원인이 남음 |
| 스트림은 확정된 분만 쓰고 MERGE | 체결이 더 올 수 있는 분은 쓰지 않음. Spark가 실패 뒤 같은 묶음을 다시 써도 같은 결과 |
| Airflow는 순서만, 계산은 기존 명령 | DAG 전용 처리 코드가 없어 손으로 돌린 결과와 DAG 결과가 같은 코드에서 나옴. 명령이 멱등이라 다시 시도가 안전 |
| decimal | 합계가 더하는 순서와 상관없이 같아야 검증이 가능 |

## 8. 돌려 보기

**먼저 키 없이 (Docker Desktop만 있으면, 약 2분)** — 합성 종목 `DEMOUSDT`로 처음부터 끝까지 돌리고 정답과 맞춥니다
([기록](docs/SAMPLE.md)). `data/sample/` 아래에만 씁니다.

```bash
pip install -e ".[dev,lake]"
python -m mdp.sample.demo --with-stream
```

진짜 데이터로:

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
python -m mdp.query.check_silver --symbol BTCUSDT --month 2024-03                            # DuckDB 교차 검산
python -m mdp.query.sql "SELECT trade_date, rows, suspect FROM silver_agg_trades_audit_daily" # 아무 SQL
python -m mdp.bench.main --symbol BTCUSDT --month 2024-03 --rounds 2                       # 벤치마크 (약 10분)
docker compose run --rm spark python -m mdp.gold.bars_1m --symbol BTCUSDT --month 2024-03  # Gold 1분봉
python -m mdp.serve.bars_1m --symbol BTCUSDT --month 2024-03                               # PostgreSQL로 복사 · 체크섬
python -m mdp.reprocess.main --symbol BTCUSDT --month 2024-03 --volume-tolerance-pct 0.0001 # 규칙 바꿔 재처리 · 비교
```

Airflow로 한 번에 (화면 http://127.0.0.1:58090):

```bash
docker compose --profile airflow up -d   # Bronze 를 다른 디스크에 두었다면 MDP_BRONZE_ROOT 를 먼저 설정
docker exec mdp-airflow-1 airflow dags test mdp_monthly_pipeline 2024-03-01
```

- `MDP_BRONZE_ROOT`를 빠뜨리고 켜면 Airflow가 빈 `data/lake/bronze`를 보고 다시 받으려 합니다
  (예: PowerShell `$env:MDP_BRONZE_ROOT = "F:/mdp/bronze"` 뒤에 `up -d`).

Kafka 스트림 (Kafka는 이 PC에서 `127.0.0.1:57092`):

```bash
docker compose --profile stream up -d
docker compose run --rm spark python -m mdp.stream.produce --source replay --symbol BTCUSDT --start 2024-03-01T00:00 --end 2024-03-02T00:05 --fresh-topic --duplicate-every 997 --delay-every 1009 --delay-ms 5000
docker compose run --rm spark python -m mdp.stream.bars_1m --trigger available-now --fresh
python -m mdp.stream.check --symbol BTCUSDT --start 2024-03-01T00:00 --end 2024-03-02T00:00   # 배치 봉과 맞대기
docker compose run --rm spark python -m mdp.reconcile.daily --symbol BTCUSDT --date 2024-03-01 --repair   # 매일 대사 · 메우기
docker compose run --rm spark python -m mdp.stream.produce --source live --symbol BTCUSDT --duration-s 300   # 실시간 (키 없음)
```

시험:

```bash
pytest tests/mdp                                                   # 파이프라인 시험 85개 (PostgreSQL 시험 4개는 MDP_TEST_PG_DSN 있을 때)
docker compose run --rm spark python -m pytest tests/mdp/spark -q  # Spark 시험 12개 (컨테이너 안)
ruff check src tests && mypy
```

## 9. 기술 스택 · 구조

| 구분 | 사용 |
|---|---|
| 언어 · 품질 | Python 3.11+ (Spark 이미지 안은 3.12), pytest, mypy(strict), ruff |
| 처리 | Apache Spark 4.0.1, Delta Lake 4.0.1 |
| 저장 · 조회 | Delta(파일 레이크하우스), PostgreSQL 17 (실행 기록), DuckDB 2.0 (Spark 없이 SQL 조회 · 교차 검산) |
| 오케스트레이션 | Apache Airflow 3.1 (standalone, LocalExecutor, Spark 단계는 DockerOperator) |
| 스트림 | Apache Kafka 4.3 (KRaft 1대), Spark Structured Streaming (RocksDB 상태 저장소), confluent-kafka · websockets |
| 실행 환경 | Docker Compose 프로필 (`db`, `spark`, `airflow`, `stream`) |

```text
src/mdp/
├─ bronze/    받기(fetch) · 증분 받기와 백필(sync) · 원천 파일 이름표
├─ silver/    출처 고르기 · 검증 · 풀기(sources) · Silver 체결 감사(agg_trades)
├─ gold/      Gold 1분봉 · 공식 봉 대조(bars_1m)
├─ serve/     Gold → PostgreSQL 복사 · 양쪽 체크섬(bars_1m)
├─ reprocess/ 재처리 — 다시 만들기 · 버전 비교(diff) · 되돌리기(restore)
├─ spark/     Delta를 켠 로컬 Spark 세션 · 표 경로
├─ runlog/    실행 기록(PostgreSQL) · 마이그레이션 SQL
├─ query/     DuckDB 조회 층 — Delta 뷰 · 교차 검산(check_silver) · SQL 실행
├─ bench/     벤치마크 — 같은 과제를 기존 추출기 · DuckDB · Spark로, 시간 · 메모리 · 결과 비교
├─ orchestrate/ Airflow DAG 도우미 — 돌릴 달 정하기 · Spark 컨테이너에 붙일 호스트 경로 찾기
├─ stream/    Kafka 보내기(실시간 · 재생 + 결함 섞기) · 스트림 1분봉 · 배치 봉과 맞대기
├─ reconcile/ 매일 대사 — 원인 나누기(끊김 · 늦음 · 중복) · 배치 봉으로 메우기 · PostgreSQL 기록
├─ sample/    키 없는 시연 — 합성 Bronze(결함 5종 + 정답지) · 한 번에 돌리기 · DuckDB 로 정답 맞추기
└─ verify/    기존 연구 추출기와 비교
manifests/bronze/   받은 원천 파일의 체크섬 목록 (데이터 대신 커밋)
airflow/dags/       Airflow DAG (월 파이프라인)
docker/spark/       Spark + Delta 이미지
docker/airflow/     Airflow 이미지 (+ psycopg · DuckDB)
docs/               규칙 · 결과 · 발견 기록 · 로드맵 · 그림 원본
```

## 10. 로드맵

| 상태 | 단계 |
|---|---|
| ✅ 끝 | Bronze 받기 · 실행 기록 · 증분 받기와 백필 · Silver 체결 감사 · DuckDB 조회 층 · 벤치마크 · Gold 1분봉 + PostgreSQL 복사 · 재처리 시연 · Airflow 오케스트레이션 · Kafka 스트림 1분봉 · 매일 대사 · 키 없는 샘플 시연 |
| ⏳ 다음 | 설계 결정 기록(ADR) |
| ⏳ 공개 전 | 옮겨 온 실주문 코드 걷어 내기 · CI 다듬기 · 화면 |

단계별 전체 기록은 [docs/ROADMAP.md](docs/ROADMAP.md)에 있습니다.

## 데이터와 재배포

- **데이터 파일은 커밋하지 않습니다.** 받는 코드와 체크섬 목록만 둡니다.
- 바이낸스 공개 아카이브(data.binance.vision) 데이터의 재배포 조건은 **확인 필요**입니다. 확인 전까지 받은 데이터와 그 가공물을 공개하지 않습니다.
