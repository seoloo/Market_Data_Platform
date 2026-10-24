# 0002 — 표 형식은 Delta Lake

- 상태: 채택 · 2026-10-21
- 관련: [Silver](../SILVER_AGG_TRADES.md) · [재처리](../REPROCESSING.md) · [스트림](../STREAM.md)

## 맥락

Silver · Gold는 '같은 달을 다시 만들면 그 달만 바뀌어야' 하고(멱등), 바꾸기 전 내용과 비교 · 되돌리기가 돼야
한다. 스트림은 묶음마다 같은 분을 덮어써야 한다. 그러면서 Spark 없이도 읽혀야 한다(DuckDB).

## 결정

Delta Lake 4.0.1 (Spark 4.0.1과 짝)로 모든 Silver · Gold 표를 쓴다.

| 쓰는 기능 | 어디서 |
|---|---|
| 달 단위 `replaceWhere` | Silver · Gold — 같은 달을 다시 쓰면 그 달 칸만 바뀜 |
| 시간여행 `versionAsOf` · `delta_scan(..., version =>)` | 재처리 전후 비교(4-5), Airflow 재실행 비교(4-6), 대사 뒤 확인(5-2) |
| `RESTORE` | 재처리 되돌리기(4-5) |
| `MERGE` | 스트림 봉(5-1) · 대사 메우기(5-2) |
| 트랜잭션 로그 | 쓰다 죽어도 반쪽 표가 보이지 않음 |

## 다른 안

| 안 | 버린 이유 |
|---|---|
| 그냥 Parquet 폴더 | 트랜잭션 · 시간여행 · MERGE가 없다. 달을 바꿔 끼우는 중간에 읽으면 반쪽이 보인다 |
| Apache Iceberg | 같은 일(트랜잭션 · 시간여행 · MERGE)을 할 수 있다. Delta를 고른 것은 Spark 4.0 짝과 DuckDB `delta_scan` 읽기를 먼저 확인했기 때문이고, Iceberg로 같은 일을 재 보지는 않았다(비교는 확인 필요) |
| PostgreSQL을 주 저장소로 | [0004](0004-postgres-small-tables.md) — 크기 때문에 안 된다 |

## 치르는 값

- **버전 짝**: Delta 4.0.x는 Spark 4.0.x와만 맞는다. pypi 최신 `delta-spark` 4.4.x는 더 새 Spark를 요구해 쓰지
  않았다(2026-10-21 확인). 이미지에 jar를 SHA-1로 대조해 고정했다.
- 표마다 버전이 쌓인다(스트림 표는 묶음마다 MERGE라 하루 재생에 버전 70여 개). 오래된 버전 정리(`VACUUM`)는
  아직 하지 않았다.
- 빈 내용으로 다시 쓰면 Delta가 커밋을 건너뛰어 버전이 늘지 않는다 — 버전 번호로 '다시 썼다'를 판단하면 안 된다
  (4-6에서 확인).

## 근거

- 재처리 · 되돌리기 · 행 단위 비교가 모두 이 기능들 위에서 됐다(4-5: 바뀐 것 10행, RESTORE 뒤 0행 차이).
- DuckDB가 같은 표를 `delta_scan`으로 Spark 없이 읽었다(4-2 검산 9.5초).
