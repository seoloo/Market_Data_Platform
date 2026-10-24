# 로드맵 · 진행 기록

README 에는 요약만 두고, 단계별 전체 기록은 여기에 둔다. 단계가 끝날 때마다 이 표와
[README](../README.md) · [그림](diagrams/README.md)을 함께 고친다.

처음에는 BTCUSDT 선물 한 달치(2024-03)로 받기부터 Gold까지 끝까지 통과시킨 뒤 기간을 늘린다.

## 파이프라인

| 단계 | 내용 | 상태 |
|---|---|---|
| 3-1 | Bronze 받기 — 바이낸스 공개 아카이브를 받은 그대로 보관, 체크섬 대조, 체크섬 목록 | ✅ BTCUSDT 2024-03 (체결 · 1분봉 · funding) |
| 3-2 | 실행 기록 — 로컬 PostgreSQL(Docker Compose 프로필 `db`) | ✅ 작업마다 코드 버전 · 인자 해시 · 산출물 체크섬 · 최대 메모리 |
| 3-3 | 증분 받기 · 백필 — 새 파일 · 빠진 날만, 두 번 돌려도 같은 결과 | ✅ 끝난 달은 월 파일 · 이번 달은 일 파일, `--days` 백필 |
| 4-1 | Silver 감사 — Spark · Delta 로 중복 · 빈 곳 · 잘림, 걸러 낸 것은 격리 표로 | ✅ [기록](SILVER_AGG_TRADES.md) · 연구 216 전수 추출과 일치, 빠른 추출의 [버그 발견](findings/2026-10-21-qflow216-check-minute-chunk-boundary.md) |
| 4-2 | DuckDB 조회 층 — Silver Delta 표를 Spark 없이 SQL 로 읽고 감사 결과 검산 | ✅ `mdp.query` · 불변 조건 5 개 + 날 감사 재계산, 2024-03 위반 0 · 31 일 소수점까지 일치 (9.5 초) |
| 4-3 | 벤치마크 — 기존 스크립트 · Spark · DuckDB 로 같은 한 달치 (시간 · 최대 메모리 · 결과 일치) | ✅ [기록](BENCHMARK.md) · DuckDB 12 초 · Spark 105 초 · 전수 추출 134 초, 셋 다 같은 답 |
| 4-4 | Gold 1분봉 — 체결로 만들고 PostgreSQL 조회용 표로 복사 | ✅ [기록](GOLD_BARS_1M.md) · 44,640 행, 연구 216 의 '1,110 분 · −3.5e-6' 재현, PostgreSQL 복사 체크섬 같음 |
| 4-5 | 재처리 시연 — 규칙 변경 → 다시 만들기 → Delta 시간여행으로 전후 비교 | ✅ [기록](REPROCESSING.md) · 기준 0.5 % → 0.0001 %: 날 감사 10 행 `suspect` 만 변경, RESTORE 뒤 7 개 표 모두 원래와 같음 |
| 4-6 | Airflow 로 묶기 — 받기 → Silver → 검산 → Gold → 복사, 재시도 · 백필 | ✅ [기록](AIRFLOW.md) · DAG 로 2024-03 끝까지 14 분, 다시 만든 7 개 표가 이전과 행 단위로 같음 |
| 5-1 | Kafka 스트림 1분봉 | ✅ [기록](STREAM.md) · 중복 · 지연을 섞은 하루 재생에서 배치 봉과 1,440 분 모두 같음, 늦은 체결로 바뀐 26 분을 `ids_contiguous` 가 정확히 표시, 실시간(키 없음) 925 건 확인 |
| 5-2 | 매일 대사 — 스트림 봉 = 배치 봉, 결과는 PostgreSQL | ✅ [기록](RECONCILE.md) · 원인 나누기(끊김 21 분 · 늦음 426 분 · 중복 0) 숫자가 보낸 쪽 13,897 · Spark 678 과 정확히 같음, 메운 뒤 1,440 분 같음. 실시간 하루 대사는 아직 |
| 6-1 | 키 없는 샘플 시연 — 합성 데이터로 끝까지, 결과 확인은 DuckDB | ✅ [기록](SAMPLE.md) · `python -m mdp.sample.demo --with-stream` 하나로 107 초, 넣은 결함 5 종 · 대사까지 정답지와 12/12 |
| 6-2 | 결정 기록(ADR) — 왜 Delta · 왜 Spark 와 DuckDB 둘 다 · 왜 PostgreSQL 은 작은 표만 | ✅ [11 개](adr/README.md) · 결정마다 맥락 · 다른 안 · 치르는 값 · 잰 근거 |

## 저장소 정리 (공개 전)

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 원본 연구 저장소를 히스토리째 복사 | ✅ |
| 1 | DE 코드(시장 데이터 · 저장 계층 · 체결 추출) 이름 풀기 · `[쓰임]`/`[이유]` 주석 — 동작 변경 없음 | ✅ [기록](DE_REFACTOR.md) |
| 2 · 6-3 | 원본에서 옮겨 온 실주문 · 실계좌 코드와 화면 걷어 내기, 문서 정리, 새 상태 화면 | ✅ 코드 묶음 1 ~ 6 · 원본 문서 40 개에 '역사 기록' 안내 · 개인 경로 가리기 31 개 파일 · AGENTS 포트폴리오용 · [상태 화면](STATUS_PAGE.md) (2026-10-24) — [목록 · 진행 기록](LIVE_REMOVAL_PLAN.md) |

- 코드: 거래소 · 증권사에 키로 접속하는 코드 · 실거래 실행 스크립트 · 화면 · 주문 실행기를 모두 지웠다(묶음 2 ~ 6 에서 304 개 파일,
  시험 4,528 → 3,597). 남은 것은 키 없는 공개 시세 · 가짜 거래소 · Paper · 연구 코드다. 남은 코드에 서명 · 키 헤더 ·
  실거래소 주문 구현이 없다.
- 원본 운영 기록(`PROGRESS.md` · `docs/BRIEFING_FOR_LLM.md` · `docs/current/` · 진행 기록 보관본 · 리팩토링 계획)은
  실제 입금액 같은 개인 금융 정보가 있어 뺐다(2026-10-24 사용자 결정).
- git 이력에는 지운 실주문 코드 · 개인 정보가 남아 있다 → 공개는 **이력 없는 공개용 브랜치**로 한다(사용자 결정 —
  아래 '공개용 브랜치'). 저장소 공개 전환 자체는 사용자 확인 뒤.
- 공개 전에 함께 볼 것: 개인 정보가 남은 문서(목록은 [LIVE_REMOVAL_PLAN.md](LIVE_REMOVAL_PLAN.md) '이번 범위 밖'),
  git 히스토리를 어떻게 할지.

## CI 다듬기

지금 CI(`.github/workflows/ci.yml` · `secrets.yml`)는 원본 저장소 것을 그대로 옮겨 왔다. 2026-10-23 에 CI 와 같은
조건(Docker `python:3.11` · `pip install -e ".[dev]"`)으로 돌려 보니, 이 저장소의 새 코드는 통과하지만 원본에서
옮겨 온 코드 때문에 `main` 에서부터 실패한다 — ruff 16 건 · 서식 3 파일(연구 스크립트 3 개), mypy 23 건, Python 3.11
에서 안 도는 연구 시험 4 모듈, 리눅스에서 출력이 달라지는 기준 시험 1 개.

| 단계 | 내용 | 상태 |
|---|---|---|
| C-1 | 옮겨 온 코드의 CI 실패 정리 — 대부분은 6-3(실거래 코드 걷어 내기)에서 파일째 사라진다. 남는 연구 스크립트는 고치거나 검사 대상에서 뺀다(연구 숫자 · 결론은 바꾸지 않음) | ✅ 2026-10-24 · CI 조건(파이썬 3.11 · 리눅스)에서 6 단계 모두 통과 — mypy 23 → 0 (형 표기 · 시험만 고침), 3.11 에서 못 읽던 연구 시험 4 모듈은 실행 중 만들던 `array[int]` 형 별칭 2 곳을 글자로, ruff 16 · 서식 3 은 연구 스크립트 3 개를 검사에서 뺌(고치면 결과가 바뀔 수 있음), `reports/` 도 검사에서 뺌 |
| C-2 | 로컬 CI 재현 스크립트를 저장소에 — push 전에 CI 와 같은 컨테이너에서 같은 단계를 돌린다 | ✅ `bash scripts/ci_local.sh` — `python:3.11-slim` 에 git 이 아는 파일만 넘겨 6 단계, 약 3.5 분(두 번째부터) |
| C-3 | Spark 시험(`tests/mdp/spark`)도 CI 에서 — Spark 이미지 빌드 캐시로 시간을 줄인다 | 🟡 `.github/workflows/spark.yml` 작성 (파이프라인 · Spark 시험 · 이미지가 바뀔 때만, 이미지 층 캐시). GitHub 에서는 아직 안 돌려 봄 — Actions 사용 시간 한도 · main/PR 에서만 돈다 |
| C-4 | `main` 보호 규칙 — PR 로만 합치고, 필수 검사(CI · Secrets) 통과해야 합치기 | ⏸ GitHub 무료 요금제의 비공개 저장소는 보호 규칙을 쓸 수 없다(API 403 'Upgrade to GitHub Pro or make this repository public', 2026-10-24 확인). 공개할 때 적용 — 아래 명령 |

- GitHub Actions 사용 시간이 모자라 2026-10-23 PR 검사가 시작도 못 했다("spending limit"). 초기화는 11/01 로 알고 있다.
- C-4 를 공개 뒤 적용하는 명령 (필수 검사 = `check` · `gitleaks`, PR 로만 합치기, 강제 push 막기):
  `gh api -X PUT repos/<소유자>/<저장소>/branches/main/protection --input protection.json` — 내용은
  `{"required_status_checks": {"strict": true, "contexts": ["check", "gitleaks"]}, "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0}, "restrictions": null,
  "allow_force_pushes": false, "allow_deletions": false}` (검사 이름은 공개 저장소에서 한 번 돈 뒤 확인 필요).
- PR 이 열려 있으면 그 브랜치에 push 할 때마다 CI 가 돈다 — 검사 시간을 아끼려면 합칠 준비가 됐을 때 PR 을 연다.
