# 05 — A1 G6 원천의 시점별 공개 가능성 감사 (2026-09-23)

> 사전등록된 TRAIN 손익을 읽기 전에 수행했다. 이 문서는 **원천 후보 조사**이지
> 일정 완전성 인증이나 `CalendarCoverage`가 아니다. DB 삽입·TRAIN 실행 없음.

## 확인한 공식 원천

| 종류 | 확인한 자료 | 당시 알 수 있던 것 | 아직 입증되지 않은 것 |
|---|---|---|---|
| FOMC | [2021 회의 일정 발표](https://www.federalreserve.gov/newsevents/pressreleases/monetary20200702a.htm) (2020-07-02 10:00 EDT), [2022 회의 일정 발표](https://www.federalreserve.gov/newsevents/pressreleases/monetary20210604a.htm) (2021-06-04 11:00 EDT) | 해당 연도 **잠정 회의 날짜**는 미리 공지됨 | 두 발표문만으로 각 회의의 **성명 발표 시각** 및 추후 일정 변경 이력은 확정 불가. [2021 회의 기록](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)의 사후 시각을 최초 공개 시각으로 소급하면 안 됨 |
| CPI·NFP | [BLS 2021 일정](https://www.bls.gov/schedule/2021/), [2022 일정](https://www.bls.gov/schedule/2022/) | 행사별 날짜·시간(미 동부시각)과 월별 `Last Modified Date` 표기 | 월별 수정일이 어떤 행사 **이후**인 경우 해당 행사 시각이 그 전부터 그대로 알려져 있었는지 증명하지 못함. 수정일의 시각·이전 판본도 확인 필요 |
| PCE | [BEA 2021 일정의 2020-12 발행본](https://apps.bea.gov/scb/issues/2020/12-december/1220-news-releases-2021.htm), [BEA 2022 일정의 2021-12 발행본](https://apps.bea.gov/scb/issues/2021/12-december/1221-news-releases-2022.htm) | 월간 Personal Income and Outlays(=PCE 포함) 날짜와 통상 08:30 ET 발표를 연초 전 자료에서 확인 | 2021 페이지에는 2021-02-09 수정 기록이 있음. 변경된 개별 날짜의 원래 판본·공지 시각은 별도 확인 필요 |
| POWELL | [연준 2021 연설 보관본](https://www.federalreserve.gov/newsevents/2021-speeches.htm) 및 [2021-06-17 사전 행사 공지](https://www.federalreserve.gov/newsevents/pressreleases/other20210617a.htm) | 과거 연설 목록과 일부 사전 행사 공지의 존재. 이 공지는 2021-07-14에 **행사 날짜가 수정**됐다고 명시함 | **모든** Powell 발언·기자회견의 포함 기준, 정확한 시간, 그 시간이 사전 공지된 시각, 누락 여부. 사후 연설 목록이나 수정된 공지의 현재 내용만으로 PIT 달력을 만들 수 없음 |

`vintage_ms`는 페이지가 **현재** 보여주는 발표일도, 웹 크롤링 시각도 아니다.
해당 일정 판본이 실제로 공개되어 판단자가 알 수 있었던 시각이다.
날짜만 증명되면 보수적으로 그 날짜의 동부시각 종료 이후부터 알려진 것으로
취급할 수 있으나, 이는 개별 행사 시각·수정 이력이 함께 입증될 때만 유효하다.
서머타임이 있으므로 ET를 고정 UTC 오프셋으로 변환하지 않는다.

## 구현·데이터 상태

- `MacroEventRepository.schedule_revisions()`는 `vintage_ms <= as_of_ms` 행만
  읽고, `period`(동일 이벤트 판본 키)·`source`가 비면 거부한다.
  **읽은 행의 존재나 건수로 coverage를 생성하지 않는다.**
- 원천별 스냅샷·변경 이력을 대조하고 2021-01-03~2022-12-31의 다섯 종류를
  독립적으로 확인한 뒤에만 `CalendarCoverage`를 수동 승인해야 한다.
  원천 URL, 공개 시각 근거, 각 판본의 해시·수정 내역이 필요하다.
- 실제 실행 스크립트의 DB 읽기→coverage 검증→`BacktestEngine` 주입은
  아직 연결하지 않았다. 읽기 어댑터가 생겼다는 사실만으로 G6는 통과하지 않는다.
- 현재 시장 DB의 `macro_events`는 0건이다. 실제 일정 파일·DB 삽입,
  coverage 인증, A1 TRAIN 백테스트를 하지 않았다.

## 2026-09-23 추가 대조 — 전월 보도자료의 `Next release`

현재 연도별 달력보다 강한 PIT 증거를 세 건 확인했다. 모두 해당 발표 **전에**
공개된 공식 보도자료에 다음 발표의 날짜·시각이 명시돼 있다.

| 종류 | 다음 발표 | 사전 공지 원천과 최초 사용 가능 시각 |
|---|---|---|
| CPI | 2021-01-13 08:30 EST | [2020-12-10 CPI 보도자료](https://www.bls.gov/news.release/archives/cpi_12102020.htm)의 `scheduled to be released`; 해당 자료 공개 2020-12-10 08:30 EST 이후 |
| NFP | 2021-01-08 08:30 EST | [2020-12-04 고용 보도자료](https://www.bls.gov/news.release/archives/empsit_12042020.htm)의 `scheduled to be released`; 해당 자료 공개 2020-12-04 08:30 EST 이후 |
| PCE | 2021-01-29 08:30 EST | [2020-12-23 Personal Income and Outlays 보도자료](https://www.bea.gov/news/2020/personal-income-and-outlays-november-2020)의 `Next release`; 해당 자료 공개 2020-12-23 08:30 EST 이후 |

BEA 보도자료 **25개**(2020-11~2022-11 대상)를 `scripts/audit_bea_pce_schedule.py`로
읽어 인접한 `Next release`와 다음 달 현재 웹 발표 머리말을 **24쌍** 대조했다.
**23쌍 일치, 1쌍 불일치**다. 해당 건의 [2021-09 자료](https://www.bea.gov/news/2021/personal-income-and-outlays-september-2021)는
2021-11-24 **10:00 EST**를 예고한다. [2021-10 웹 본문](https://www.bea.gov/news/2021/personal-income-and-outlays-october-2021)은
같은 날 **08:30 EST**라 적지만 [동일 발표의 공식 PDF](https://www.bea.gov/sites/default/files/2021-11/pi1021.pdf)는
**10:00 EST**다. 이는 일정 변경이라고 단정하기보다 **웹/원본 불일치**로
격리한다. PDF를 포함한 독립 확인 전 이 건을 `MATCH`로 승격하지 않는다.

**독립 확인 결과 (2026-09-23, Claude):** 세 공식 원천이 모두 **10:00 A.M. EST**다.

| 원천 | 공개 시점 | 표기 |
|---|---|---|
| [BEA 2021 발표 일정](https://apps.bea.gov/scb/issues/2020/12-december/1220-news-releases-2021.htm) | 2020-12 발행, 2021-02-09 수정 (발표 9개월 전) | Personal Income and Outlays, October 2021 — `10:00 a.m.` |
| [2021-09 자료](https://www.bea.gov/news/2021/personal-income-and-outlays-september-2021) `Next release` | 2021-10-29 08:30 EDT | 2021-11-24 10:00 A.M. EST |
| [공식 PDF](https://www.bea.gov/sites/default/files/2021-11/pi1021.pdf) 첫 줄 | 발표 당일 | `EMBARGOED UNTIL RELEASE AT 10:00 A.M. EST, WEDNESDAY, NOVEMBER 24, 2021` |

08:30으로 적힌 것은 현재 웹 머리말뿐이다. 같은 날 GDP 2차 추정이 08:30에
나왔으므로 웹 템플릿 표기 오류로 판단한다. 이 건은 감사기의 `RESOLUTIONS`에
근거 URL 3개와 공식 시각을 기록해 **`RESOLVED`**로 표시한다. `MATCH`로
바꾸지 않았고, 예고 시각이 기록된 공식 시각과 다르면 다시 `DISCREPANCY`다.
재실행 결과 **23 MATCH / 1 RESOLVED / 0 DISCREPANCY**, 종료 코드 0.

24쌍은 기준월 2020-12~2022-11, 즉 발표 **2021-01-29 ~ 2022-12-23**이며
TRAIN(2021-01-03~2022-12-31) 안의 월간 PCE 발표를 모두 덮는다.
`vintage_ms`는 직전 보도자료 공개 시각으로 둔다. 연간 일정표가 더 일찍
알렸더라도 늦은 판본을 쓰는 쪽이 보수적이며, 모든 발표가 판본보다 약 한 달 뒤라
블랙아웃 창을 놓치지 않는다.

감사기는 후보를 표준 출력으로만 내고, 해결되지 않은 불일치가 있으면 비정상 종료한다.
고정 UTC 오프셋을 쓰지 않고 `America/New_York`의 서머타임으로 변환한다.
이 24쌍은 PCE만 해당한다. CPI/NFP/FOMC/POWELL의 전체 기간 완전성은
여전히 미입증이며, 어떤 후보도 시장 DB에 넣거나 coverage로 인증하지 않았다.

## 2026-09-23 FOMC 성명 시각 — 회의 전 웹 아카이브 스냅샷 (Claude)

G6의 FOMC는 원 설계([05-risk-engine G6](../archive/design/05-risk-engine.md)
"FOMC 금리 결정")대로 **정책 성명**이다. 의사록은 포함하지 않는다.

- **날짜:** [2021 일정 발표](https://www.federalreserve.gov/newsevents/pressreleases/monetary20200702a.htm)
  (2020-07-02)·[2022 일정 발표](https://www.federalreserve.gov/newsevents/pressreleases/monetary20210604a.htm)
  (2021-06-04)의 화-수 회의 16회. 성명은 둘째 날.
- **완전성:** [연준 FOMC 캘린더](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)에
  2021·2022년 각 정기회의 8회만 있고 임시회의·전화회의·서면결의 표기가 없다.
- **시각의 사전 공개 판본:** 연준 월별 캘린더(`/newsevents/YYYY-month.htm`)의
  FOMC 절이 `2:00 p.m. FOMC Meeting`을 표기한다. 현재 페이지는 사후 판본이므로,
  Internet Archive CDX로 **성명일 동부시각 0시 이전의 마지막 스냅샷**을 골라
  그 스냅샷 시각을 `vintage_ms`로 쓴다. 가장 늦은 사전 판본이라 보수적이다.
- **실제 발표:** 성명 보도자료(`monetaryYYYYMMDDa.htm`)의 `For release at 2:00 p.m. EST/EDT`.
  날짜와 EST/EDT 표기가 실제 서머타임과 다르면 실패한다.

`scripts/audit_fomc_schedule.py` 실행 결과 **16/16 MATCH**, 종료 코드 0.
스냅샷은 성명 **0.7~51.9일 전**(가장 짧은 2021-07은 전날 저녁, 가장 긴 2022-07은
6월 초 스냅샷)이며 모두 블랙아웃 시작(성명 60분 전)보다 앞선다. 각 원문 SHA-256을 출력한다.
CSV는 표준 출력으로만 냈고 DB에 넣지 않았다.

## 2026-09-23 POWELL — FOMC 기자회견 (Claude)

**범위(사용자 결정 A):** 원 설계 G6 표대로 **FOMC 후 Powell 기자회견만**이다.
연설·의회 증언은 포함하지 않는다. 2021~2022 FOMC 16회 모두 기자회견이 있었다
(연준 FOMC 캘린더의 `Press Conference` 항목, 회의 전 월별 캘린더 스냅샷에도 성명일에 표기).

월별 캘린더는 기자회견을 **시각 없이** 표기한다. 시각의 사전 판본으로는
연준 홈페이지 배너(`Watch Chair Powell's press conference live on <날짜> at <시각>`,
2022 하반기는 `live at <시각> on <날짜>`)의 Internet Archive 스냅샷을 쓴다.
`scripts/audit_powell_press_schedule.py`가 성명일 14일 전부터 POWELL 블랙아웃 시작
(`before_min`=15, config에서 읽음) 전까지 최근 스냅샷을 역순으로 최대 8개 확인한다.

| 결과 | 회의 |
|---|---|
| **ANNOUNCED 13건** — 모두 14:30 ET, 블랙아웃 시작보다 29~336분 앞선 당일 스냅샷 | 2021-03·04·07·09·11·12, 2022-01·03·05·07·09·11·12 |
| **NOT_FOUND 3건** — 차단 창 전 마지막 스냅샷에 이전 회의 다시 보기 배너만 있음 | 2021-01-27, 2021-06-16, 2022-06-15 |

실제 시작 시각을 적은 공식 기록은 찾지 못해 예고 확인(`ANNOUNCED`)까지만 판정한다.
13건이 모두 14:30이었다는 관행으로 나머지 3건의 시각을 채우면 PIT 근거가 아니라
사후 추론이 되므로 하지 않았다.

**NOT_FOUND 3건 처리 (2026-09-23 사용자 결정 1 — fail-closed):** 세 날의
**동부시각 하루 전체**를 달력 사용 불가로 둔다. 판단 시각이 그 구간이면 G6가
`EVENT_BLACKOUT`으로 신규 진입을 거부한다(청산은 막지 않음). 시각을 추정하지 않는다.

- 구현: `backtest/calendar.py`에 `CoverageGap`(구간·종류·사유)을 추가하고
  `CalendarCoverage.gaps`로 받는다. 공백 종류가 감사 종류에 없거나 감사 범위를 벗어나면
  생성이 거부된다. 경계는 포함한다. 요청한 블랙아웃 종류와 겹치지 않는 공백은 무시한다.
- 감사기: 승인한 3일(`APPROVED_GAP_DAYS`)만 `GAP`으로 표시하고 공백 구간
  (`gap_from_ms`/`gap_to_ms`)을 출력한다. 그 밖의 날에서 판본을 못 찾으면 여전히
  `NOT_FOUND`·종료 코드 2다(아카이브 일시 장애가 조용히 공백이 되지 않게).
- 표본 영향: 2년 중 3일(성명일)이며, 그날의 신규 진입만 막는다.

## 2026-09-23 CPI·NFP — 사용자 저장 BLS 보도자료 (Claude)

사용자가 브라우저로 저장한 BLS 보도자료 50개(CPI 25·고용보고서 25,
2020-12 ~ 2022-12 발표)를 `scripts/audit_bls_schedule.py`로 로컬 감사했다.
네트워크 접근은 없다. 파일 이름은 저장 시 제각각이었으므로 **내용의 엠바고 날짜로**
`cpi_MMDDYYYY.htm`/`empsit_MMDDYYYY.htm`로 바꿨고 SHA-256은 변하지 않았다.
바꾼 이름은 `--print-urls` 목록 50개와 정확히 일치한다.

- 파서 보정 1건: 고용보고서의 엠바고 문장은 시각 앞에 보도자료 번호가 있다
  (`embargoed until USDL-21-0582 8:30 a.m. (ET) Friday, April 2, 2021`). 테스트로 고정.
- 결과: **CPI 24/24 MATCH, NFP 24/24 MATCH**, 종료 코드 0.
  사건은 CPI 2021-01-13 ~ 2022-12-13, NFP 2021-01-08 ~ 2022-12-02로 TRAIN의 두 지표
  발표를 모두 덮는다. `vintage_ms`는 직전 보도자료 공개 시각(약 한 달 전)이다.
- 원본 HTML은 `data/macro_sources/bls/`(gitignore)에만 있다. 재현에는 파일별 SHA-256을 쓴다.

## 2026-09-23 적재 준비 — G6 달력 매니페스트 (Claude)

감사 CSV 4개(`data/macro_sources/audits/`, gitignore)를 `scripts/build_g6_calendar.py build`가
검증해 **[매니페스트](../../config/macro_calendar/a1_train_g6_2021_2022.json)** 하나로 묶는다(Git 추적).
BEA 감사기에도 원문 SHA-256 열을 추가해 5종 모두 원천 URL과 해시를 남긴다.

- 범위: TRAIN `2021-01-03 00:00 ~ 2022-12-31 23:59:59.999 UTC`, 종류 5개(config 블랙아웃 키).
- 항목(TRAIN 안): **FOMC 16 · CPI 24 · PCE 24 · NFP 24 · POWELL 13**, 공백 3일(POWELL).
- 빌드 검사: 모든 항목의 `vintage_ms`가 그 종류의 블랙아웃 시작 이전, 월간 지표 연속,
  FOMC = 연준 회의 16회, 기자회견 + 공백 = 회의 16회, 허용되지 않은 감사 상태 거부.
- `CalendarManifest`(`backtest/calendar_manifest.py`)의 digest가
  `CalendarCoverage.source_digest`가 된다. 파일의 digest가 내용과 다르면 읽기를 거부한다.
  digest: `aaa3de7381947ced9ec3733c9a0c2755ef806f288afe87c83d677d1131ba6e97`.
- `macro_events.source`에는 해시 열이 없으므로 `원천URL#sha256=<해시>`로 저장한다.
- **적재 dry-run** (`load`, 읽기 전용): `data/trading.db`에 **추가 예정 101건**
  (CPI 24·FOMC 16·NFP 24·PCE 24·POWELL 13), 이미 동일 0건, 충돌 0건. DB는 여전히 0건.
  실제 쓰기는 `load --apply`이며 사용자 승인 전에는 실행하지 않는다. 같은 판본 키의
  다른 내용이 이미 있으면 충돌로 중단한다.

## 다음 검증 순서

1. ~~FOMC의 회의 날짜와 성명 발표 시각~~ — 완료(위).
2. ~~BLS CPI·NFP~~ — 완료(위). 접근 방식 결정 기록:
   현재 BLS의 직접 HTTP 자동 요청은 403을 반환했으므로, 다른 공식 접근
   경로가 확인되기 전까지 근거 없는 자동 수집이나 우회 크롤링은 하지 않는다.
   웹 읽기 도구로 개별 보도자료는 열람된다(2020-12-10 CPI 확인).
   **2026-09-23 결정·시험:** 사용자 승인으로 신원을 밝힌 User-Agent
   (`auto-trading-schedule-audit/1.0`, 개인정보 없음)로 1건 시험했으나 403이다.
   차단 본문은 "BLS usage policy에 맞지 않는 bot 활동은 금지"라고 명시한다.
   헤더 위장 등 추가 시도는 우회이므로 중단하고, 사용자 결정에 따라
   **사용자가 브라우저로 저장한 HTML을 로컬에서 감사**한다.
   `scripts/audit_bls_schedule.py`는 네트워크에 접근하지 않고
   `data/macro_sources/bls/`(gitignore)의 파일을 읽어 파일별 SHA-256과 함께
   CPI·NFP 예고/실제 발표를 대조한다. `--print-urls`는 저장할 50개
   (CPI·NFP 각 TRAIN 발표 24건 + 첫 예고 1건) 목록이며, 안내용일 뿐 근거는 파일 자체다.
   BLS의 `(ET)`는 EST/EDT를 밝히지 않으므로 날짜의 서머타임으로 변환한다.
3. PCE 완료. POWELL은 기자회견 13/16 ANNOUNCED + 승인된 공백 3일(fail-closed).
   미확인 시각을 조용히 채우거나 G6 종류를 줄이지 않는다.
4. 기간·종류별 누락과 중복, ET→UTC 변환, 판본 재생을 독립 감사한 다음
   coverage를 인증한다. 그 전에는 G6 fail-closed와 TRAIN 보류를 유지한다.
