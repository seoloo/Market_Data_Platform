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

## 다음 검증 순서

1. FOMC의 회의 날짜와 **성명 발표 시각**을 분리해 각 시점의 사전 공지판본을 찾는다.
2. BLS 월별 수정일이 행사보다 이른 CPI·NFP만 먼저 후보로 삼고,
   나머지는 더 이른 공식 판본 또는 전월 발표문의 `Next release`를 찾는다.
3. BEA 연초 발표본과 변경 공지를 대조하고, Powell의 이벤트 범위·공지판본을
   정의한다. 미확인 시간대를 조용히 제외하거나 G6 종류를 줄이지 않는다.
4. 기간·종류별 누락과 중복, ET→UTC 변환, 판본 재생을 독립 감사한 다음
   coverage를 인증한다. 그 전에는 G6 fail-closed와 TRAIN 보류를 유지한다.
