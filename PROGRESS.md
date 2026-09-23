# 작업 현황

최종 갱신: 2026-09-23 · 작성: Claude (Codex/sol 작업 인계).
현재: **Phase 0 — A1 종료(DATA_INSUFFICIENT) / 다음 연구 탐색: 워뇨띠 거래 내역(v2 완료)**.
D22는 중단·제외. 승인된 수익 전략 없음. **A1 TRAIN 손익 통계 미열람(거부 집계로 DD 정지 사실은 노출)**.

## 이번 작업 (Claude — 워뇨띠 v2 왕복 재구성)

- `scripts/explore_wonyotti_trips.py`(테스트 5건): XBTUSD 체결 941,007건 → 왕복 2,589건.
  재구성 순손익 2,007.99 BTC vs 지갑 XBTUSD 실현손익 2,007.08 BTC(차이 0.05%)로 재구성 검증.
- 연도별: 보유 중앙값 12분(2018) → 15.5시간(2021), 잔고 대비 배율 중앙값 3.8 → 0.72배, 2배 초과 64% → 5.6%.
  XBTUSD 숏 1,263 · 롱 745 BTC(2021 숏 663 vs 롱 114). 4배 초과 구간 누적 −368 BTC.
- 요약 주장 대부분 재현. 불일치: 최다 수익 배율 구간(요약 1~2배, 실제 2~4배), 승·패 보유 수치(방향만 일치).
  2021-05-19 −44%는 일일 정산 때문에 5/20 행에 기록됨. 2018 청산은 (날짜·상품) 24건·3월 9건.
- 지갑 잔고 열 18%가 스프레드시트 지수 표기로 정밀도 손실(배율은 근사). [06 §4](docs/research/06-wonyotti-exploration.md).
- 다음: v3 — 진입 직전 시장 상태(움직임·변동성·funding)와 거래당 bps 수익, 2020~2021 역행폭(Binance 1분봉).

## 이전 작업 (Claude — A1 종료·워뇨띠 탐색 시작)

- **A1 종료 (사용자 결정 1):** [RESEARCH_HISTORY](docs/current/RESEARCH_HISTORY.md)에 A1 `DATA_INSUFFICIENT` 기록
  (계좌 DD 10% 정지로 표본 절단, 손익 통계 미열람, 재시도 금지 사항). [BACKTEST_AND_TESTING](docs/current/BACKTEST_AND_TESTING.md)에
  "예상 표본에 계좌 정지·G5 상호작용 포함" 교훈 절 추가. CURRENT_SPEC 상태 갱신. 02 상태 `종료`.
- **워뇨띠 탐색 시작:** [06 탐색 문서](docs/research/06-wonyotti-exploration.md) — 목적(가설 생성, 복제 금지),
  파일 SHA-256, 노출 기록(2018-03~2021-12), `90일 서한` 연구 제외.
- `scripts/explore_wonyotti.py`(표준 라이브러리, 11초, 테스트 2건): 유튜브 요약 주장 재현 —
  실현손익 3,537.32 BTC·37/46개월·상위 20일 62.4%·상위 50일 제외 −245 BTC·지정가 97.2%·시장가 5회·스톱 43·
  출금 2,814.5 BTC **일치**. "2019 이후 청산 0회"는 **부정확**(XBTUSD 소액 청산 2019-09-24·2020-03-12).
  요약에 없던 사실: 2018~2020 체결의 절반 이상이 테이커(편도 7.5bps), 수수료 순지출 35.6·24.1·40.2 BTC,
  2021만 리베이트 우위 −22 BTC.
- 다음: v2 XBTUSD 포지션 재구성(보유시간·배율·롱숏 손익·손절 행동) → v3 시장 상태 결합 → 가설 후보 최대 3개 비교.

## 이전 작업 (Claude — A1 TRAIN 1회 실행)

- 사용자 승인으로 커밋 `916bbf2`의 깨끗한 트리에서 `run_a1_train.py run` 1회 실행(주 arm 34분·대조군 39분).
- **결과: `DATA_INSUFFICIENT`** — 주 arm 실제 거래 **102건** < 146(§11), 대조군 45건. T1~T5 미계산·미열람.
- 거부 사유 집계(판정 파일)만 확인: 주 arm `SAME_DIRECTION_LIMIT` 194·`VOL_TOO_LOW` 153·**`MAX_DRAWDOWN` 125**,
  대조군 **`MAX_DRAWDOWN` 1,014**·`SAME_DIRECTION_LIMIT` 637. → **두 arm 모두 누적 DD 10% 정지로 표본이 잘렸다.**
  이 집계가 성과 방향을 간접 노출했음을 [02 §12](docs/research/02-a1-preregistration.md)에 기록.
- 원인: 예상 표본(162)이 계좌 수준 정지를 반영하지 않은 설계 결함. `reports/a1/train/`(gitignore)에 판정·원거래 보관.
- **A1 후속 방향은 사용자 결정 대기.**

## 이전 작업 (Claude — A1 TRAIN 판정·실행 스크립트)

- `src/auto_trading/backtest/a1_verdict.py`: 02 §7·§11 판정을 상수로 고정. **주 arm 거래 146건 미만이면
  통계 함수를 호출하지 않고 `DATA_INSUFFICIENT`**. 이상이면 T1(순손익 평균−2SE>0)·T2(비용 전 평균≥42.44)·
  T3(대조군 대비 차이−2SE>0, Welch)·T4(2021·2022 각 평균>0)·T5(LONG·SHORT 각 평균>0 = 원문
  "각각 부호 동일"을 T1과 결합한 동치)·강제청산 0 → `PASS`/`REJECT`. 테스트 7건.
- `scripts/run_a1_train.py`: `run`은 dirty 트리·기존 판정 파일이 있으면 거부(TRAIN 1회),
  DB `macro_events`가 매니페스트와 정확히 같을 때만 달력 주입, 주 arm·대조군 같은 엔진·데이터로 재생,
  판정·메타데이터(git·config·매니페스트 digest·필터·veto)·원거래를 `reports/a1/train/`에 기록. 테스트 3건.
  `smoke`는 달력 미주입(G6 fail-closed)으로 거래 0건을 확인하며 속도만 잰다.
- smoke 7일: 판단 2,017회·16초(≈8ms/회) → **TRAIN arm당 약 28분, 두 arm 약 1시간** 예상. 거래 0건 확인.
- **TRAIN은 아직 실행하지 않았다.** 사용자 확인 후 커밋된 깨끗한 트리에서 `run` 1회.

## 이전 작업 (Claude — G6 일정 DB 적재·외부 자료 접수)

- 사용자 승인으로 `build_g6_calendar.py load --apply`: `data/trading.db`의 `macro_events`에 **101건 적재**
  (한 트랜잭션, 적재 전 0건·프로세스 없음·WAL 0바이트 확인). 재 dry-run 동일 101·충돌 0,
  저장소 읽기 결과 = 매니페스트 일정, 표본 달력 판정 9건 기대대로. 7.6GB DB 전체 백업은 하지 않았다.
- **외부 자료 접수(분석 전):** 사용자가 워뇨띠 BitMEX 내보내기를 `data/external/wonyotti/`(gitignore)에 저장.
  체결 CSV 4개(2018-03~2021-12, 약 149만 행, `transacttime` μs, XBTUSD·ETHUSD·선물·funding 행 혼재),
  지갑 CSV(4,388행, 시각 열은 엑셀 손상·날짜 정상), 유튜브 요약·서한 txt(2차 자료). **형식만 확인, 손익 미열람.**
  사용자 지시: A1 판정 후 연구. 분석 시 2018~2021은 해당 파생 가설의 **노출 구간**이 되므로
  검증은 2023 이후·전향 구간에서 한다.

## 이전 작업 (Claude — G6 달력 매니페스트·적재 dry-run)

- 감사 CSV 4개를 `data/macro_sources/audits/`(gitignore)에 모으고, BEA 감사기에 원문 SHA-256 열 추가
  (재실행 23 MATCH·1 RESOLVED 동일).
- `src/auto_trading/backtest/calendar_manifest.py`: 항목·범위·공백을 고정하는 `CalendarManifest`,
  digest = `CalendarCoverage.source_digest`, 파일 digest 불일치 시 거부. 테스트 5건.
- `scripts/build_g6_calendar.py`: `build`(감사 규칙 검증 → [매니페스트](config/macro_calendar/a1_train_g6_2021_2022.json))
  · `load`(기본 dry-run, `--apply`만 DB 쓰기). 테스트 5건.
- 매니페스트: TRAIN 안 FOMC 16·CPI 24·PCE 24·NFP 24·POWELL 13 + 공백 3일,
  digest `aaa3de73…6e97`. **dry-run: `data/trading.db`에 추가 예정 101건, 충돌 0, DB 변경 없음.**
- **다음: 사용자 승인 후 `load --apply` → 실행 스크립트에서 DB 읽기 + 매니페스트 digest 대조 → 달력 주입.**

## 이전 작업 (Claude — A1 최소 거래 수 고정·BLS 로컬 감사기)

- 사용자 승인으로 [02 §11](docs/research/02-a1-preregistration.md)에 **TRAIN 주 arm 실제 거래
  146건 미만 → `DATA_INSUFFICIENT`**를 결과 열람 전에 고정. 거래 수를 먼저 확인하고 미달이면
  손익 통계를 계산·열람하지 않는다. 표본을 늘리려고 파라미터·게이트·기간을 바꾸지 않는다.
- BLS: 사용자 승인으로 신원을 밝힌 User-Agent를 1회 시험 → 403, 차단 본문에 "이용 정책 외 bot 금지".
  우회하지 않고 사용자 수동 저장 방식으로 전환. `scripts/audit_bls_schedule.py`(네트워크 없음,
  파일별 SHA-256) + 테스트 8건. 저장 폴더 `data/macro_sources/bls/`(gitignore)를 만들었다.
- **CPI·NFP 완료:** 사용자가 저장한 BLS HTML 50개를 로컬 감사. 파일명을 내용의 엠바고 날짜로
  `cpi_/empsit_MMDDYYYY.htm`로 정리(SHA-256 불변, 안내 목록 50개와 일치). 고용보고서 엠바고 문장의
  보도자료 번호(`USDL-21-0582`)를 파서가 허용하도록 보정·테스트. **CPI 24/24, NFP 24/24 MATCH**.
- **FOMC 완료:** `scripts/audit_fomc_schedule.py` — 회의 전 Internet Archive 스냅샷의 연준 월별
  캘린더 시각(판본)과 성명 보도자료 `For release at`(실제)을 대조, **16/16 MATCH**. 2021·2022 임시회의 없음.
  G6 FOMC = 정책 성명(원 설계), 의사록 제외. 테스트 8건.
- **POWELL (사용자 결정 A: FOMC 기자회견만):** `scripts/audit_powell_press_schedule.py` — 연준 홈페이지
  배너 스냅샷이 블랙아웃 시작 전 시각을 알렸는지 확인. **13/16 ANNOUNCED(모두 14:30 ET)**,
  **3건 판본 없음**(2021-01-27, 2021-06-16, 2022-06-15) → 사용자 결정 (1): 그날 동부시각 하루 전체
  fail-closed. `CoverageGap`/`CalendarCoverage.gaps` 추가(달력 사용 불가 → `EVENT_BLACKOUT`),
  감사기는 승인된 3일만 `GAP`과 공백 구간 출력, 그 밖의 미발견은 실패. 테스트: 달력 3·엔진 1·감사기 8건.

## 이전 작업 (Claude — sol 인계, BEA PCE 일정 감사 완료)

- sol이 토큰 한도로 멈춘 미커밋 작업을 이어받았다: `scripts/audit_bea_pce_schedule.py`,
  `tests/scripts/`, 05 문서 추가분, `tzdata`(Windows) 의존성.
- 2021-11-24 PCE 불일치를 독립 확인했다. BEA 2021 연간 일정표(2021-02-09 수정본)·
  직전 보도자료 예고·공식 PDF가 모두 **10:00 EST**이고 현재 웹 머리말만 08:30이다.
  감사기에 근거 URL과 함께 `RESOLVED`로 기록했다(해결 기록과 시각이 다르면 계속 실패).
  재실행: **23 MATCH / 1 RESOLVED / 0 DISCREPANCY**. TRAIN 내 PCE 발표 24건 전부 대조 완료.
- ruff 규약 정리(로캘 의존 `strptime` 제거, `pairwise`), 테스트 5 → 8건.
- 직전 커밋 `de957ad`의 `tests/storage/test_macro_events.py` fixture 반환 타입을
  `Iterator`로 정정(mypy strict 실패 해소, 동작 변경 없음).
- 시장 DB 삽입·coverage 인증·TRAIN 실행 없음.

## 이전 작업 (Codex — G6 공식 원천 1차 감사·DB 읽기 경로)

- [05 원천 감사](docs/research/05-a1-macro-pit-source-audit.md)에 2021~2022
  FOMC/CPI/NFP/PCE/POWELL 공식 일정의 **행사 시각 대 사전 공개 시각** 차이와
  아직 입증되지 않은 판본·누락 범위를 기록했다. 연준 Powell 사전 공지의
  행사 날짜 수정 사례도 확인했다. 어떤 원천도 아직 전체 기간 coverage로 인증하지 않았다.
- `MacroEventRepository.schedule_revisions()` 추가: `vintage_ms` 상한을 SQL에서
  적용하고 이벤트 키·원천 결측을 거부한다. 읽은 행만으로 `CalendarCoverage`를
  만들지 않는다. 자료형은 Pure Core로 분리했다.
- 시장 DB 삽입·삭제 없음, 실제 A1 TRAIN/VALIDATION/TEST 성과 미열람.

## 이전 작업 (Codex — 사전등록 개정 승인·G6 입력 감사)

- 사용자 승인으로 [02 §11](docs/research/02-a1-preregistration.md)에 **T2 비용 전
  42.44bps**, TRAIN `2021-01-03~2022-12-31`, T4 `2021·2022`를 결과 열람 전에
  고정. T1·G5 등 기존 게이트는 유지. 원문과 [03 감사](docs/research/03-a1-pretrain-audit.md)는
  변경 이력을 위해 보존했다.
- 체결 시뮬레이터가 다음 1분 기준가를 거래에 기록하도록 확장했다.
  슬리피지·수수료·funding 전 `pre_cost_pnl/bps`와 실제 비용 후 `net_bps`를
  같은 진입 기준 명목가로 재현할 수 있다. 체결 가격·손익 계산 자체는 유지.
- [04 G6 준비 상태](docs/research/04-a1-g6-readiness.md): 승인 TRAIN의 가격봉
  1m/5m/1h은 기대 그리드 개수와 일치, 2021~2022 funding 2,190건이며
  9시간 초과 공백 없음. 그러나 `macro_events` **0건**이고 검증된 coverage도 없어,
  G6를 우회하지 않고 TRAIN을 **DATA_INSUFFICIENT**로 보류. 이후 순수 연결은 추가됨.
- 합성 A1 통합 테스트에서 다음 1분 체결·2시간 청산·funding 결측·
  이벤트 일정 결측 fail-closed를 확인했다. 실제 TRAIN 손익은 실행하지 않았다.

## 이전 작업 (Codex — 2020 데이터 감사와 A1 엔진 경계)

- DB 읽기 전용 확인: Binance USD-M BTCUSDT 1h·5m 첫 봉은
  `2020-01-01 00:00 UTC`, 2019년 1h 데이터 0개. A1 G5 스냅샷 요구
  8,824개 1h 봉의 최초 충족은 `2021-01-02 15:59:59 UTC`.
  공식 Binance 공개 데이터 도구도 2020-01-01 이후 아카이브를 안내한다.
- 수익 평균 없이 예상 표본만 추가: 2h 주 arm 2021년 75개, 2022년 87개;
  대조군 155개, 158개. 제안 TRAIN `2021-01-03~2022-12-31`에서는
  **주 arm 162 / 대조군 313 예상**. 실제 G1~G7/체결 후 더 적을 수 있다.
- A1 합성 백테스트 통합 테스트 추가: 동일 `decide()` 경로의 다음 1분 체결,
  2시간 청산, funding 결측 시 진입 거부를 검증. 운영 G5 설정은 변경하지 않았다.
- [03 감사](docs/research/03-a1-pretrain-audit.md)에 데이터 출처·정확한
  warm-up 시각·재등록 제안을 추가. **T2와 TRAIN/T4 변경은 미승인**으로 유지.

## 이전 작업 (Codex — A1 구현 전 감사와 공통 경로)

- [03 A1 TRAIN 전 감사](docs/research/03-a1-pretrain-audit.md) 작성. 02의
  `직전 6h 고가/저가`와 과거 코드의 `직전 6h 종가 극값` 불일치를 찾아
  feature/측정 스크립트를 고가·저가 기준으로 정정. 평균 수익은 보지 않았다.
- 손절을 1%까지 WIDEN하는 기존 A1 설계는 사용자 요구와 충돌. A1에만
  `REDUCE_POSITION` 적용: 2σ 전략 손절은 유지하고 1%는 사이징 분모 하한으로 사용.
  H1 기본 설정은 변경하지 않았다.
- 정정 설계 입력: 원 트리거 1,510개, 2h 거래 가능 **예상** 526개,
  TRAIN/VAL/TEST 예상 227/160/139, 비용 대리치 19.91bps,
  OOS 100거래 총효과 문턱 42.44bps. G1~G7/실제 체결을 포함한 수치는 아님.
- A1 사전 진입 비용 추정은 H1 기본 13bps 대신 20bps로 상향했다.
  성과 미열람 트리거 비용 대리치 19.91bps 이상으로, 사이징에서 보수적으로 사용한다.
- A1을 `features/a1.py` + 공통 `decide()`에 연결. 목표가 없이 2시간 청산,
  H1 기본 경로 유지. 백테스트는 같은 함수/인과적 사전계산 신호를 사용한다.
  미검증 edge 우회는 A1 **BACKTEST** 연구에만 허용한다.
- 추가 테스트: 손절 유지/목표가 없음, 데이터 불량 중 청산,
  신호 미래 무관성, 연구용 edge 우회의 라이브 금지.
- **TRAIN 보류:** 원문 T2의 총/순 효과 단위 충돌 및 G5 365일 warm-up으로
  2020년 실제 평가 불가. 기준을 소급 변경하지 않고 사용자 결정 필요.

## 이전 작업 (Claude — A1 원 사전등록)

- **A1 사전등록 완료** — [02-a1-preregistration](docs/research/02-a1-preregistration.md).
  사용자 결정: 후보 **A** · C8 **선택지 1**. 전략 코드는 아직 만들지 않았다.
- 실행 전 실측 두 가지가 설계를 바꿨다.
  - `min_stop_distance=1%` 가 2h 손절의 **100%** 를 WIDEN → 35.1bps 는 실행 불가능한
    전략의 수치였다. 하한 적용 후 필요 효과 **40.9bps** (비용의 2.14배).
  - `same_direction_limit=2` 가 트리거의 **65%** 를 차단 (H1 을 막았던 그 한도).
    연 279 → **92회**. 표본·검정력을 전부 한도 적용 후 값으로 다시 계산했다.
- 대조군(수축 조건 제거) 정의·판정 고정: 주 arm 이 대조군을 **12.8bps 이상** 앞서야 한다.
  못 넘으면 A1 은 H1 변형이므로 기각.
- C1·C2·C4·C8 을 사전등록 안에서 처리. C2 는 1봉(5분) 지연과 셔플 null 로 분리 확정.

## 이전 작업 (OOS 구속조건 발견)

- **OOS 게이트가 진짜 구속조건임을 발견** ([§6](docs/research/01-candidate-comparison.md)).
  Phase 4 의 `실제 거래 n≥100` 으로 확인 가능한 최소 효과는 A 35.1bps · B 53.0bps 인데,
  TRAIN 은 23.9bps 도 잡아낸다. **23~35bps 구간은 TRAIN 통과 후 OOS 확인 불가.** → C8
- 손절 적용 거래당 손익 SD 실측: A 2h·0.5σ 에서 73.6 → 56.7 (−23%). 도움은 되나 부족.
- 검정력 게이트에 **"TRAIN 과 OOS 중 큰 쪽이 기준"** 규칙 추가. 봉 SD 가 아니라
  거래당 손익 SD 와 Phase 4 거래 수 요건을 쓰도록 명시.
- C7·C8 을 CURRENT_SPEC 미해결 등록부에 정식 등록.

## 이전 작업 (트리거 시점 재측정)

- **트리거 시점 재측정으로 추천이 뒤집혔다** ([§5](docs/research/01-candidate-comparison.md)).
  B(청산 캐스케이드)는 진입 직후 1분 움직임이 전체의 **3.06배**, 2h SD 가 92.9 → 139.9 로
  둘 다 악화. 실측 비용 28.3bps 적용 시 문턱 27.3 → **39.9bps**. **추천을 B 에서 A 로 바꾼다.**
- 19 → 20 (maker 비용 낙관 → 신호 시점 재측정으로 번복)과 같은 패턴이며,
  이번에는 **사전등록 전에** 잡았다.
- 트리거 정의를 `src/auto_trading/backtest/triggers.py` 로 분리 (테스트 21건).
  빈도를 세는 쪽과 SD 를 재는 쪽이 갈라지면 게이트가 조용히 무효가 되기 때문.
- 그 테스트가 결함을 잡았다 — 돌파 봉 자신이 HTF 추세 판정에 들어가 역방향 급변이
  추세를 뒤집었다. 추세를 돌파 직전 기준으로 수정 (A 빈도 282 → 279회, 영향 미미).

## 이전 작업 (후보 비교)

- **후보 3개 비교 완료** — [01-candidate-comparison](docs/research/01-candidate-comparison.md).
  게이트로 C(다일 추세 추종) 탈락, A(변동성 수축 돌파)·B(청산 캐스케이드 반전) 통과.
- BTC 잡음 바닥 실측: 증명 가능한 최소 효과 = `18 + 0.77 × 보유시간(h)` bps. 국면에 강건.
- **변동성 수축은 검정력을 개선하지 않음을 실측** (SD −21%, 표본 −63% → 순손해).
  후보 A 의 근거를 "잡음이 작아서" 에서 "효과가 커야" 로 정정했다.
- 트리거 빈도 실측: A 연 282회 · B 연 269회 · C 연 26회.
- 사용자 선택 대기. 선택 후 **트리거 시점** SD·비용 재측정이 사전등록의 선행 조건.

## 이전 작업 (검정력 게이트)

- 사전등록 **검정력 게이트** 도입: 가용 표본으로 판정할 수 없는 가설은 사전등록하지 않는다.
  [BACKTEST_AND_TESTING](docs/current/BACKTEST_AND_TESTING.md) 의 새 절, 계산은 `src/auto_trading/backtest/power.py`.
- 옛 식 `(2·SD/Δ)²` 가 **검정력 50%** 였음을 확인하고 기본값을 0.80 으로 올렸다 (표본 2.02배).
  H2 재현 검정은 443개가 필요한데 189개로 등록해 검정력 44% 였다 — 설계 단계의 실패였다.
- 후보 비교표에 검정력 판정과 `필요 거래 수 → 도달 기간` 을 필수 항목으로 추가.
- 문서 재편 `2500ac2` 독립 검증: 코드 변경 0, 링크 0건 깨짐, 보관본 본문 일치, C1 실재 확인.

## 이전 작업 (문서 재편)

- 승인된 계획에 따라 Current 5개 문서 작성, 역할별 단일 기준으로 정리.
- 정리 직전 기존 34개 문서의 본문 보관(보관 안내 및 상대 링크만 정리).
- D22 초안과 여러 문서에 분산된 변경을 Archive에 보존하고 현행 전략/로드맵에서 제외.
- 기존 경로는 짧은 안내문으로 유지, AGENTS/CLAUDE/README의 기본 읽기 경로 갱신.
- 위험·검증 기준·연구 결과·storage 정책을 분리하고 구현과 요구사항의 차이를 명시.
- 삭제/DB 변경/코드 변경/커밋/푸시 없음.

## 다음 작업 — Phase 1 / Phase 3

**Phase 1 — 2021~2022 PIT 이벤트 일정 확보·품질 감사:** FOMC/CPI/PCE/NFP/POWELL
종류별 당시 공개 시각과 연속 구간을 검증한다. **PCE 완료**(24건, 05 문서).
**5종 원천 대조·매니페스트·DB 적재(101건)·실행 스크립트 완료.** 남은 순서: 커밋 → `run_a1_train.py run` 1회(사용자 승인).
현 DB의 `macro_events`는 비어 있다.
**Phase 3 — 백테스트 G6 실데이터 연결:** 시점별 `calendar_available`과
`event_blackout_active`를 기존 Risk 경로에 전달하는 순수 로직은 추가했다.
DB 읽기 어댑터는 추가했다. 기간별 coverage 감사·원천 provenance 확인과
실제 일정 데이터 삽입, 실행 스크립트의 달력 주입을 완료해야 한다.
이 두 작업과 Phase 2 실제 피드 속도·인과성 검증 후 Phase 4 TRAIN 1회 실행.
§3.3의 8개 파라미터 및 위험 한도는 변경하지 않는다.

## 단계 상태

| Phase | 상태 |
|---|---|
| 0 | **A1 사전등록 개정 승인·고정.** TRAIN 미실행 |
| 1~3 | 기존 기반 및 A1 공통 경로·G6 순수 연결 추가. 실제 G6 달력 데이터/어댑터 미완료, 새 전략 게이트 통과 아님 |
| 4 | H1/H2/H3/R1 기각. 통과 전략 없음 |
| 5~9 | 실거래 실행·Paper·Testnet·소액/본 운용 미완료, 진입 승인 없음 |

## 미해결

- C1: A1에는 손절 유지/위험 축소 적용. H1 기본 WIDEN은 그대로이며 전략별 정합성 검토 필요.
- C2: 지연 검사 1일/1봉과 유지/소멸 판정 충돌. 게이트 완화 없이 사전 확정 필요.
- C3: 과거 기반 완료 표시와 데이터 시작점/계좌 snapshot 게이트 불일치 감사.
- C4: 무제한 보유의 split 경계/purge/embargo 설계.
- C5: 새 전략·운용 자본 미정.
- C6: 실제 거래소 API/보호주문·복구 구현 및 운영 검증 대기.
- C7: D22 제외가 사용자 의도인지 미확인. 2026-09-22 `요구사항 확정` → 다음 날 `EXCLUDED`.
- C8: **해소** — 사용자 승인으로 T2를 비용 전 42.44bps로 개정.
- A1-G5: **해소** — 2020년 T4 표본 0 확인 후, 사용자 승인으로 2021~2022 TRAIN/T4 재등록. G5 완화 없음.
- A1-G6: **해소** — `macro_events` 101건, 실행 시 매니페스트 digest 대조 후 주입.
  PCE 24·CPI 24·NFP 24·FOMC 16·POWELL 13 대조 완료, POWELL 3일은 승인된 공백(fail-closed).
  POWELL 설정 창(±15분)은 원 설계 "시작~종료+15분"보다 짧다(기록만, 한도 변경 없음). coverage 인증 없이는 TRAIN 불가.
- A1-N: **해소** — 사용자 승인으로 주 arm 실제 거래 146건 미만이면 DATA_INSUFFICIENT (02 §11). 원 분석:
  개정 입력(TRAIN SD 95.64·비용 19.91·효과 42.44bps·검정력 80%)으로 재계산하면 필요 ≈146건,
  예상 162건은 G1~G7 적용 전이다(G5 VOL_TOO_LOW가 수축 국면과 겹침).
- A1-P (2026-09-23 Claude 분석): 트리거 6시간 잠금은 과거 트리거에 의존한다. 백테스트는 전체 구간
  사전 스캔, 라이브 경로는 9,123봉 창 재스캔이라 창 시작점에 따라 달라질 수 있다. 현 테스트는 미래 봉
  추가만 검사. A1은 backtest 전용이라 TRAIN 영향은 없으나 Phase 6 parity 전에 해결 필요.
- C1: **A1 에 직접 영향** — 2h 손절의 100% 가 WIDEN 된다. 현재 설정 그대로 사전등록했다.
- C2·C4: **A1 범위에서 해소** (사전등록 §8). 다른 전략에는 재확정 필요.

## 검증 기록

이번 작업 (Claude — A1 TRAIN 판정·실행 스크립트, 2026-09-23):

- smoke 7일: 거래 0건(G6 fail-closed) 확인, 16초. TRAIN 손익 미실행·미열람.
- 전체 pytest **1,196건**, leakage **44건**, mypy strict **150개** 파일,
  ruff check·format --check **176개** 파일, import-linter 4/4, `git diff --check` 통과.

이번 작업 (Claude — G6 달력 매니페스트·적재 dry-run, 2026-09-23):

- 매니페스트 빌드 2회 digest 동일. `load` dry-run 후 `macro_events` 0건 그대로(읽기 전용 확인).
- 전체 pytest **1,186건**, leakage **44건**, mypy strict **147개** 파일,
  ruff check·format --check **172개** 파일, import-linter 4/4, `git diff --check` 통과.

이전 작업 (Claude — A1 최소 거래 수·BLS 로컬 감사기, 2026-09-23):

- BLS 요청은 사용자 승인 1회만(403). 이후 네트워크 접근 없음.
- 실데이터: FOMC 16/16 MATCH, BLS 로컬 CPI·NFP 48/48 MATCH, POWELL 13 ANNOUNCED·3 GAP(승인된 공백, 종료 0).
- 전체 pytest **1,176건**(스크립트 33·달력/엔진 공백 4건 포함), leakage **44건**, mypy strict **144개** 파일,
  ruff check·format --check **168개** 파일, import-linter 4/4, `git diff --check` 통과.

이전 작업 (Claude — BEA PCE 감사 인계·완료, 2026-09-23):

- 감사기 실데이터 재실행 2회: 수정 전 23 MATCH / 1 DISCREPANCY(sol 결과 재현),
  해결 기록 후 23 MATCH / 1 RESOLVED / 0 DISCREPANCY, 종료 코드 0. 출력은 표준 출력만 사용.
- 전체 pytest **1,147건** 통과, leakage 마커 **44건** 통과, mypy strict **141개** 파일 통과,
  ruff check·format --check **162개** 파일, import-linter 4/4, `git diff --check` 통과.

이전 작업 (G6 공식 원천 1차 감사·DB 읽기, 2026-09-23):

- 원천 감사는 Fed/BLS/BEA 공식 자료로만 수행. TRAIN 손익·시장 DB 변경 없음.
- 거시 일정 저장소 단위·인과성 테스트 5건 추가. 전체 pytest 통과,
  leakage 마커 **44건** 통과, mypy strict **34개** 파일 통과,
  ruff check·format --check **160개** 파일·import-linter 4/4 통과.

이번 작업 (A1 사전등록 개정 및 G6 순수 연결, 2026-09-23):

- 사용자 승인으로 T2를 비용 전 42.44bps로, TRAIN을 2021-01-03~2022-12-31로,
  T4를 2021·2022년 부호 검사로 사전 고정했다. 성과 결과는 열람하지 않았다.
- A1 체결·거래 기록에 슬리피지 전 기준가와 비용 전/후 bps를 분리했다.
  기존 체결·손익 경로는 유지했다.
- G6의 감사 범위·이벤트 공개 시각·일정 수정 판본을 반영하는 순수 달력과
  A1 백테스트 연결을 추가했다. 달력 미주입은 fail-closed. 실제 DB는
  `macro_events` 0건이므로 TRAIN은 미실행이다.
- 전체 pytest 통과, leakage 마커 43건 통과, mypy strict 38개 파일 통과,
  ruff check 통과, import-linter 4/4 통과. 포맷 수정 후
  `ruff format --check .` 157개 파일 통과, `git diff --check` 통과.

이번 작업 (검정력 게이트):

- pytest 1,109건 통과 (power 28건 + triggers 21건 신규), leakage 마커 43건 통과.
- mypy 93개 파일, ruff check/format --check 146개, import-linter 4/4 통과.
- 옛 50% 표(archive 22 §6)의 1,279·370·180·36을 `power=0.50`으로 재현하는 테스트 고정.
- 문서 링크 100개 파일 검사, 깨짐 0. config·전략 코드·시장 DB 변경 없음.

이전 작업 (문서 재편)에서 검증 완료:

- 원문 보관 34개: 작업 전 내용과 대조 일치(상단 안내/상대 링크 변환만 허용).
- Markdown 74개: 로컬 파일 링크 누락 0. git diff --check 통과.
- src/config/tests/scripts 변경 0. 시장 DB를 열거나 변경하지 않음.
- 전체 pytest 1,060건 및 leakage 마커 43건 통과.
- mypy 설정 전체 128개 파일 통과, core/strategy/risk strict 18개 파일 별도 통과.
- ruff check 통과, format --check 144개 파일 통과, import-linter 4/4 통과.
- 초기 Python 실행 접근 거부는 권한 검토 후 기존 환경으로 실행해 해결. 환경 재설치 없음.
- 옛 tests/leakage 명령은 존재하지 않는 경로였음. pytest -m leakage로 재실행하여 통과, 현행 안내 정정.

역사적 개발 이력은 [이전 PROGRESS 원문](docs/archive/design/PROGRESS.md)에 보존했다.
현재 기준과 구현 차이는 [CURRENT_SPEC](docs/current/CURRENT_SPEC.md), 과거 연구는 [RESEARCH_HISTORY](docs/current/RESEARCH_HISTORY.md)를 읽는다.
