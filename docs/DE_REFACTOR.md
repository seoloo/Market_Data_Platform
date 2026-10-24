# DE 코드 리팩토링 기록 (2026-10-21)

> 포트폴리오(데이터 엔지니어링)의 중심이 될 코드를 **사람이 읽고 설명할 수 있게** 다듬은 기록.
> 원본 실거래 리팩토링과 같은 규칙을 따른다(그 계획 문서는 원본 운영 기록이라 2026-10-24 이 저장소에서 뺐다).

## 목적 (사용자 말)

- 바이브 코딩으로 만든 코드라, 포트폴리오로 설명하려면 구현 흐름과 코드를 먼저 파악해야 한다.
- 변수 · 함수 이름을 사람이 알아보기 쉽게, 각 변수 · 함수에 **쓰임과 사용 이유**를 주석으로.

## 범위

| 한다 | 안 한다 (이유) |
|---|---|
| `core/hashing.py` | `storage/repositories/` 의 `spot_orders` · `futures_orders` · `wallet_transfers` · `risk_events` — 실주문 기록이라 포트폴리오에서 뺄 예정 |
| `storage/` — `db.py` · `migrations.py` · 저장소 7개(`candles` · `funding` · `metrics` · `meta` · `macro_events` · `paper_days` · `paper_overlay_days`) | `storage/sql/*.sql` — 이미 적용된 마이그레이션은 체크섬이 바뀌면 기존 DB 가 열리지 않는다 |
| `marketdata/` 8개 — `archive` · `backfill` · `bulk` · `funding` · `metrics` · `resample` · `integrity` · `snapshot` | `run_qflow_216.py` 의 받기 · 보완 · 표본 검산 · 실행 부분(1 분봉 구역부터 끝까지) — 다시 돌릴 일이 없는 연구 실행 코드 |
| `scripts/run_qflow_216.py` 의 **체결(aggTrades) 추출 부분** — 머리말 ~ `extract_full` | 연구 문서 · 판정 결과(`docs/research` · `reports`) — 숫자 · 결론을 바꾸지 않는다 |

## 규칙 (원본 리팩토링과 같음)

- **동작 변경 0** — 이름 · 주석 · 풀어 쓰기 · 작은 도움 함수로 나누기만. 계산 순서(소수 합산 순서 포함) 그대로.
- **바깥에서 쓰는 이름은 그대로** — 공개 함수 · 클래스 · 메서드 · 매개변수 · 상수 · 데이터 필드, DB 열 · SQL,
  pickle 로 저장되는 필드(`Extract`), 다른 파일이 import 하는 이름(`run_qflow_216` 의 `extract` · `extract_full` ·
  `months` · `_ms` 등). 파일 안에서만 쓰는 이름만 바꾼다. 바꾸기 전에 AST 로 '바깥에서 쓰는 이름' 을 모두 뽑아 확인했다.
- 파일 맨 위: **무엇 · 언제 쓰나(언제 실행) · 읽고 쓰는 것**.
- 함수 · 메서드 바로 아래 docstring: `[쓰임] …` / `[이유] …` (VS Code 에서 마우스를 올리면 보인다).
- 한두 글자 변수 금지, 긴 함수는 `# 1 단계: …` 단계 주석.

## 확인 방법

1. **전후 비교 기준(골든)** — 바꾸기 전 코드로 176 항목을 찍고(두 번 돌려 글자까지 같음을 먼저 확인), 바꾼 뒤 같은지 본다.
   값뿐 아니라 **오류 문구**도 비교한다(의도한 오류 19 항목).
   - 입력: Auto_Trading 로컬 원본 zip(BTCUSDT 2024-03 1m · 5m · 4h, funding 2024-01 ~ 03, aggTrades 일 파일 5 개)과
     합성 자료(지표 CSV · 머리줄 · 마이크로초 · 빈 곳 · 중복 · 역행을 일부러 넣은 체결 파일).
   - 체결 추출은 덩어리 크기를 32 MB · 1 MB · 151 KB 세 가지로 바꿔 덩어리 경계 처리까지 비교했다.
   - 원천 데이터 재배포 조건이 **확인 필요**라 기준 도구와 입력은 레포에 넣지 않았다(로컬 scratchpad).
2. 전체 pytest · `pytest -m leakage` · mypy(기존 15 건 그대로) · ruff · ruff format · lint-imports(계약 5 개).

## 진행 기록

| 묶음 | 파일 | 결과 |
|---|---|---|
| 1/3 | `core/hashing` · `storage` 12 개 | 골든 176 같음 · pytest 4,447 통과 · 10 건너뜀 (바꾸기 전과 같음) |
| 2/3 | `marketdata` 8 개 | 골든 176 같음 · 관련 시험 통과 |
| 3/3 | `run_qflow_216.py` 추출 부분 | 골든 176 같음(실제 일 파일 5 개 × 덩어리 3 가지 + 합성) · 연구 216 시험 통과 |

## 바뀐 이름 (파일 안에서만 쓰던 것)

| 파일 | 예전 | 지금 | 뜻 |
|---|---|---|---|
| `core/hashing.py` | `_normalize` | `_to_hashable` | 해시에 넣을 '정해진 모양' 으로 바꾸기 |
| `storage/migrations.py` | `_applied` | `_load_applied_checksums` | 이미 적용된 버전 → 체크섬 읽기 |
| 〃 | `seen` · `buffer` · `i` · `nxt` | `filename_by_version` · `current_chars` · `position` · `next_char` | |
| `storage/repositories/paper_days.py` · `paper_overlay_days.py` | `_row` · `_day` | `_day_to_row` · `_row_to_day` | 하루 기록 ↔ 표 한 줄 |
| `storage/repositories/paper_overlay_days.py` | `_dumps` | `_to_json_text` | NaN 을 막는 JSON 바꾸기 |
| `storage/repositories/candles.py` | `step` · `c` · `r` · `g` | `bar_ms` · `candle` · `row` · `gap` | |
| `marketdata/archive.py` | `BinanceVisionClient._get` | `_get_with_retry` | 404 는 None, 나머지 오류는 다시 시도 |
| 〃 | `seen` (funding) · `seen` (metrics) · `n` · `i` | `seen_funding_ms` · `entry_by_ms` · `name` · `column` | |
| `marketdata/bulk.py` | `start` · `chunk` | `batch_start` · `batch_jobs` | |
| `marketdata/resample.py` | `ratio` · `c` | `bars_per_bucket` · `member` | 긴 봉 하나에 들어가는 짧은 봉 수 |
| `marketdata/integrity.py` | `_inspect` · `t` · `a` / `b` · `counts` | `_inspect_candle` · `limits` · `original_value` / `derived_value` · `count_by_kind` | |
| `marketdata/funding.py` · `metrics.py` | `archive` | `archive_file` | |
| `scripts/run_qflow_216.py` | `parse` | `_parse_trade_line` | 체결 한 줄 → (번호, 수량, 시각, 공격 매도 여부) |
| 〃 | `_ts_line` | `_line_time_ms` | 그 줄의 체결 시각 |
| 〃 | `_seek` | `_find_first_line_at_or_after` | 시각 ≥ 목표인 첫 줄 (이분 탐색) |
| 〃 | `_lines` | `_iter_lines` | 한 줄씩 |
| 〃 | `_full_chunk` | `_scan_chunk_line_by_line` | 번호가 안 맞는 덩어리를 한 줄씩 감사 |
| 〃 | `_chunks` | `_read_complete_line_chunks` | 줄이 잘리지 않게 약 32 MB 씩 |
| 〃 | (새 도움 함수) | `_file_sha256` · `_add_chunk_windows` | `extract` 안에 있던 해시 계산 · 창 더하기를 떼어 냄 |
| 〃 | `ex` · `h` · `blk` · `buf` · `prev` · `n` · `a_*` · `z_*` · `ok` · `w0` · `w` · `pos` · `acc` · `chk` · `q` · `ts` · `ibm` | `result` · `file_hash` · `block` · `chunk` · `previous_trade` · `line_count` · `first_*` · `last_*` · `chunk_is_clean` · `first_window_ms` · `window_ms` · `start` · `window_totals` · `minute_totals` · `quantity` · `trade_ms` · `is_buyer_maker` | |
| 〃 | 숫자 그대로 쓰던 값 | `_HASH_BLOCK` · `_WINDOW_STEP` · `_HOUR_MS` · `_MICROSECOND_THRESHOLD` | 4 MB · 5 분 · 1 시간 · 마이크로초 판정 기준 |

## 디자인 패턴 (이 범위에서 쓰인 것)

| 패턴 | 곳 | 한 줄 설명 |
|---|---|---|
| Repository | `storage/repositories/*` | 표마다 읽기 · 쓰기 창구 하나 — SQL 은 이 뒤에만 있다 |
| 의존성 주입 | `BinanceVisionClient(httpx.Client)` · `Backfiller(client, repository)` | 밖에서 넣어 주므로 시험에서 가짜 네트워크 · 가짜 DB 로 바꿔 끼운다 |
| Golden Master | 이번 전후 비교 기준 | 바꾸기 전 출력을 찍어 두고 바꾼 뒤 글자까지 같은지 본다 |
