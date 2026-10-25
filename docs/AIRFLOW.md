# Airflow로 묶기 (README 4-6)

> 지금까지 손으로 차례대로 치던 명령 6개(받기 2 · Silver · 검산 · Gold · 복사)를 Airflow DAG 하나로 묶었다.
> BTCUSDT 2024-03을 DAG로 끝까지 돌려 **6개 작업 모두 성공(14분 16초)**, 만든 결과가 이전 결과와
> **7개 표 모두 행 단위로 같음**을 확인했다.
> 코드: [`airflow/dags/mdp_monthly_pipeline.py`](../airflow/dags/mdp_monthly_pipeline.py) ·
> [`src/mdp/orchestrate/`](../src/mdp/orchestrate)

## 왜 필요한가

명령마다 멱등이고 실행 기록도 남지만, **순서 · 실패 시 멈춤 · 다시 시도 · 매달 자동 실행**은 사람이 기억해야
했다. 예를 들어 검산이 실패했는데 Gold를 만들면 틀린 Silver로 봉이 만들어진다. Airflow는 이 '사람의 기억'을
코드(DAG)로 옮긴다.

## DAG

```text
bronze_trades ─┐
               ├─▶ silver ─▶ check_silver ─▶ gold ─▶ serve
bronze_klines ─┘   (Spark)    (DuckDB 검산)   (Spark)  (PostgreSQL 복사 · 체크섬)
```

| 작업 | 어디서 | 명령 |
|---|---|---|
| `bronze_trades` · `bronze_klines` | Airflow 컨테이너 | `python -m mdp.bronze.sync` (둘은 동시에) |
| `silver` | Spark 컨테이너 (DockerOperator) | `python -m mdp.silver.agg_trades` |
| `check_silver` | Airflow 컨테이너 | `python -m mdp.query.check_silver` — 실패하면 아래로 가지 않음 |
| `gold` | Spark 컨테이너 | `python -m mdp.gold.bars_1m` |
| `serve` | Airflow 컨테이너 | `python -m mdp.serve.bars_1m` |
| `report` | Airflow 컨테이너 | `python -m mdp.ui.report` — [상태 화면](STATUS_PAGE.md) 다시 만들기 (6-3 에서 더함) |

- **평소와 같은 명령을 부른다.** Airflow 전용 처리 코드는 없다. Airflow는 순서 · 다시 시도 · 기록만 맡고,
  계산은 이미 시험한 명령이 한다. 그래서 DAG 없이 손으로 돌린 결과와 DAG로 돌린 결과가 같은 코드에서 나온다.
- **다시 시도가 안전한 이유**: 각 명령이 멱등이다(받기는 체크섬 목록으로 건너뜀, Silver · Gold는 달 단위
  `replaceWhere`, 복사는 한 트랜잭션 안에서 지우고 넣기). 중간에 죽어서 다시 돌아도 결과가 두 번 쌓이지 않는다.
- 설정: 작업마다 2번 다시 시도(5분 간격) · Spark 작업은 1시간 제한 · 한 번에 한 달만(`max_active_runs=1`,
  단일 PC 메모리) · 처음에는 꺼진 상태(`paused`).

## 어느 달을 돌리나

| 실행 | 달 |
|---|---|
| 예약 `@monthly` | 데이터 구간 시작의 달 — 3월 구간 [3/1, 4/1)은 **4/1에** 돈다(달이 끝난 뒤에야 월 파일이 나오므로) |
| 수동 · 다시 돌리기 | 매개변수 `month`(`YYYY-MM`)가 우선 |
| 둘 다 없음 | 멈춤 — 어느 달인지 모르는 채로 돌리지 않는다 |

이 규칙은 한 함수([`window.py`](../src/mdp/orchestrate/window.py))가 정하고, 모든 작업이 같은 함수를 쓴다.
작업마다 달을 따로 계산하면 받기는 3월, Silver는 4월 같은 실수가 날 수 있어서다.

## 어려웠던 점 — Spark 컨테이너에 붙일 폴더 경로

Airflow는 Spark를 직접 돌리지 않고 **호스트의 Docker에게 Spark 컨테이너를 띄워 달라고** 한다(DockerOperator,
Docker 소켓 연결). 이때 붙일 폴더는 Airflow 안 경로(`/opt/mdp/lake`)가 아니라 **호스트 경로**
(`C:\…\data\lake`, `F:\mdp\bronze`)여야 한다. 컨테이너를 만드는 쪽이 호스트이기 때문이다.

호스트 경로를 설정 파일에 적으면 PC마다 고쳐야 한다. 그래서 DAG를 읽을 때 Airflow 컨테이너가 **자기 자신의
연결 정보를 Docker에게 물어**('이 안 경로는 호스트의 어디에 붙어 있나') 그 경로를 Spark 컨테이너에 그대로
넘긴다([`mounts.py`](../src/mdp/orchestrate/mounts.py)). 필요한 연결이 하나라도 없으면 Spark가 엉뚱한 곳에 쓰지
않도록 DAG를 읽는 단계에서 멈춘다. Bronze는 Spark 쪽에 **읽기 전용**으로 붙인다.

## 돌려 보기

```bash
docker compose --profile airflow build
docker compose --profile airflow up -d        # PostgreSQL 도 함께 켠다 · 화면 http://127.0.0.1:58090
```

- Bronze를 다른 디스크에 둔 경우 **켤 때 같은 위치를 알려 줘야 한다**: PowerShell은
  `$env:MDP_BRONZE_ROOT = "F:/mdp/bronze"` 뒤에 `up -d`. 빠뜨리면 Airflow가 빈 `data/lake/bronze`를 보고
  처음부터 다시 받으려 한다(받기는 체크섬 목록에 있는 파일만 건너뛰는데, 그 파일이 그 위치에 없으므로).
- Airflow 자체 기록(작업 상태 등)은 같은 PostgreSQL의 별도 DB `airflow`에 둔다. 우리 실행 기록(`mdp` DB)과 섞지 않는다.
- 화면은 이 PC(127.0.0.1)에서만 열리고 로그인 없이 모두 관리자다 — 개발용 설정이며 공개 서버에 쓰지 않는다.

```bash
docker exec mdp-airflow-1 airflow dags test mdp_monthly_pipeline 2024-03-01          # 한 번 끝까지 (예약과 같은 달)
docker exec mdp-airflow-1 airflow dags unpause mdp_monthly_pipeline                  # 매달 예약 켜기
docker exec mdp-airflow-1 airflow dags trigger mdp_monthly_pipeline -c '{"month": "2024-03"}'
docker exec mdp-airflow-1 airflow backfill create --dag-id mdp_monthly_pipeline --from-date 2024-03-01 --to-date 2024-05-01
```

## 결과 — BTCUSDT 2024-03 (2026-10-23)

`airflow dags test mdp_monthly_pipeline 2024-03-01` — DAG 실행 상태 `success`.

| 작업 | Airflow 시간 | 실행 기록(`pipeline_runs`) | 결과 |
|---|---:|---:|---|
| `bronze_trades` | 3초 | 0초 | 체크섬 목록에 있어 건너뜀(`skipped_known`) — 내려받기 0 |
| `bronze_klines` | 2초 | 0초 | 같음 |
| `silver` | 12분 35초 | 751초 | Spark 컨테이너, 66,512,993 행 |
| `check_silver` | 18초 | 15초 | 불변 조건 5개 위반 0 · 31일 감사 재계산 일치 |
| `gold` | 1분 10초 | 66초 | 44,640 봉 |
| `serve` | 7초 | 5초 | 44,640 행 복사, 양쪽 SHA-256 같음 |
| 합계 | **14분 16초** | | |

- Airflow 시간에는 컨테이너 띄우기 · 기록 쓰기가 들어가서 실행 기록 시간보다 조금 길다.
- Silver가 이전에 손으로 돌린 18분보다 짧았다(12.5분). 같은 코드 · 같은 입력이므로 그때 PC의 다른 부하 차이로
  보인다(확인 필요 — 반복 측정하지 않음).

**만든 결과가 이전과 같은가** — DAG 전 버전과 DAG가 만든 버전을 DuckDB 시간여행으로 행 단위 비교
(`mdp.reprocess.diff` 의 비교 함수, 계보 칸 `run_id` 등 제외):

| 표 | 버전 | 행 | 추가 · 삭제 · 변경 |
|---|---|---:|---|
| silver `agg_trades` | 3 → 4 | 66,512,993 | 0 · 0 · 0 |
| silver `agg_trades_audit_daily` | 3 → 4 | 31 | 0 · 0 · 0 |
| silver `agg_trades_audit_file` | 3 → 4 | 2 | 0 · 0 · 0 |
| silver `agg_trades_gaps` · `quarantine` | 0 → 0 (빈 표를 다시 써서 바뀐 것이 없어 버전이 안 늘었다) | 0 | 0 · 0 · 0 |
| gold `bars_1m` | 2 → 3 | 44,640 | 0 · 0 · 0 |
| gold `bars_1m_kline_check` | 2 → 3 | 31 | 0 · 0 · 0 |

PostgreSQL 복사본의 SHA-256(`9883fca0…`)도 4-4에서 손으로 돌렸을 때와 같다. 즉 **같은 달을 오케스트레이터로
다시 돌려도 결과가 바뀌지 않는다**(멱등) — 다시 시도 설정이 안전하다는 근거다.

## 매일 대사 DAG

`mdp_daily_reconcile` — 매일 D 일을 대사한다([대사 기록](RECONCILE.md)). 월 DAG 와 Spark 작업 만들기를
함께 쓴다(`airflow/dags/mdp_dag_common.py`).

```text
bronze_trades ─┐ (D 일 일 파일, 아직 없으면 실패 → 1 시간마다 다시, 최대 하루)
               ├─▶ silver ─▶ check_silver ─▶ gold ─▶ stream ─▶ reconcile ─▶ report
bronze_klines ─┘   (그달)                      (그달)  (원문 토픽)  (D, 메우기)  (상태 화면)
```

- **D+1 에 돈다**: 바이낸스는 D 일 일 파일을 다음 날 올린다. 받기에 `--require-all` 을 줘서 '아직 없음' 이면 종료 코드
  2 → Airflow 가 1 시간 뒤 다시 시도한다(올라오는 시각은 확인 필요라 넉넉히 하루). 없는 채로 대사까지 가지 않는다.
- **스트림 봉은 그때 한 번에**: 계속 도는 스트림 작업을 PC 에 늘 띄워 두지 않고, 원문 토픽에서 지난번 다음 메시지부터
  `--trigger available-now` 로 확정한다(체크포인트 이름 고정). 받기(`stream.produce --source live`)는 따로 돈다.
- 스트림 · 대사 작업은 Kafka 에 닿아야 해서 compose 네트워크(`mdp_default`)에 붙는다.
- 받기 전 날(2026-10-24)로 끝까지 돌려 확인: 8 개 작업 모두 성공, 4 분 20 초. 대사는 1,440 분 모두 '끊김' · 그날 체결
  842,302 건 모두 '받지 못함' — 받기를 켜기 전 날이라 맞는 답이다. 2026-10-25 부터 예약으로 켜 두었다.

## 시험

- 호스트: [`tests/mdp/test_orchestrate.py`](../tests/mdp/test_orchestrate.py) — 달 정하기(매개변수 우선 ·
  예약 구간 · 윤년 · 해 넘김 · 모르면 멈춤), 연결 목록(bind만 · 빠지면 멈춤 · Bronze 읽기 전용 · staging 볼륨).
- 컨테이너: `airflow dags list-import-errors` 가 비어 있음(DAG를 읽을 수 있음) · 위 끝까지 실행.

## 남은 것

- 실패 · 다시 시도를 일부러 일으켜 보는 시연은 하지 않았다(설정만 있음). 받기 단계는 새 달을 받으려면 약 850MB를
  내려받아야 해서 이번 범위에서 뺐다.
- 단일 PC의 standalone(스케줄러 · 화면 한 컨테이너) 구성이다. 여러 대로 나누는 구성(Celery · Kubernetes)은 범위 밖.
