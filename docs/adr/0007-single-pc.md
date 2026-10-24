# 0007 — 단일 PC 그대로, Spark는 Docker 안 `local[N]`

- 상태: 채택 · 2026-10-21
- 관련: [Spark 이미지](../../docker/spark/Dockerfile) · [`docker-compose.yml`](../../docker-compose.yml)

## 맥락

이 프로젝트는 개인 PC 한 대(Windows 11, 6코어 12스레드, RAM 32GB)에서 돈다. 포트폴리오라고 클러스터처럼
꾸미면 보기엔 그럴듯하지만, 잰 숫자가 그 구성을 설명하지 못한다.

## 결정

- Spark는 `local[N]`(드라이버 하나가 N 스레드) — 셔플 파티션도 코어 수 근처로 줄인다.
- Spark는 **Docker 안**에서: Windows에서 Spark로 파일을 쓰려면 `winutils.exe`가 따로 필요해 자주 깨진다. 리눅스
  이미지 안이면 어느 PC에서나 같다.
- 서비스는 Compose **프로필로 필요할 때만** 켠다: `db` · `spark` · `airflow` · `stream`.
- Kafka도 한 대(KRaft, 복제 1), Airflow도 standalone 한 컨테이너.

## 다른 안

| 안 | 버린 이유 |
|---|---|
| Spark 마스터 + 작업자 여러 컨테이너 | 같은 PC의 같은 코어를 나눠 쓸 뿐 빨라지지 않고, 셔플이 네트워크를 탄다 |
| Windows에 Spark 직접 설치 | winutils · 경로 · 파이썬 버전 문제 |
| 클라우드 | 키 없이 누구나 돌리는 시연(6-1)과 맞지 않고 비용이 든다 |

## 치르는 값

- **Windows 폴더 연결이 느리다**: Docker가 `C:`의 레이크를 연결해 읽고 쓰는 경로는 리눅스 안 디스크보다 느리다.
  벤치마크의 Spark 시간에 섞여 있고(크기 확인 필요), 스트림을 작은 묶음으로 돌릴 때 묶음마다 MERGE · 체크포인트를
  쓰는 고정 비용이 커서 하루치가 29분 걸렸다(5-2).
- 한 대라 메모리를 서로 나눠 쓴다: Spark 드라이버 6GB · Kafka 1GB · Airflow 약 0.9GB. 한 번에 한 달만 돌린다
  (Airflow `max_active_runs=1`).
- Windows(Hyper-V)가 잡아 둔 포트 범위가 있어, Kafka 바깥 포트를 59092 대신 57092로 바꿨다(5-1).

## 근거

- Silver 한 달 18분 · 샘플 시연 전체 107초 — 단일 PC로 충분한 크기다.
- 기간을 수십 개월로 늘리거나 종목을 여러 개로 늘리면 다시 판단한다.
