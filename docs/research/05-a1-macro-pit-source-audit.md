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

## 다음 검증 순서

1. FOMC의 회의 날짜와 **성명 발표 시각**을 분리해 각 시점의 사전 공지판본을 찾는다.
2. BLS CPI·NFP의 전월 보도자료 `scheduled to be released`를 월별로 대조한다.
   현재 BLS의 직접 HTTP 자동 요청은 403을 반환했으므로, 다른 공식 접근
   경로가 확인되기 전까지 근거 없는 자동 수집이나 우회 크롤링은 하지 않는다.
   웹 읽기 도구로 개별 보도자료는 열람된다(2020-12-10 CPI 확인). 재현 가능한
   수집 방식(스크립트 접근 조건 또는 사용자 수동 저장)은 **사용자 결정 대기**다.
3. PCE는 완료(위). Powell의 이벤트 범위·공지판본을
   정의한다. 미확인 시간대를 조용히 제외하거나 G6 종류를 줄이지 않는다.
4. 기간·종류별 누락과 중복, ET→UTC 변환, 판본 재생을 독립 감사한 다음
   coverage를 인증한다. 그 전에는 G6 fail-closed와 TRAIN 보류를 유지한다.
