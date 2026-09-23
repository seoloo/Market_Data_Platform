# 04 — A1 TRAIN 입력 데이터·G6 준비 상태 (2026-09-23)

> [02 §11](02-a1-preregistration.md#11-train-결과-열람-전-개정-2026-09-23-사용자-승인)의
> 기준이 승인된 **후**, 성과 열람 **전**에 실시한 입력 감사다. 시장 DB와
> 코드는 읽기 전용으로 확인했다. TRAIN/VALIDATION/TEST 손익은 실행·열람하지 않았다.

## 승인된 TRAIN `2021-01-03 ~ 2022-12-31`의 입력

| 입력 | 확인 결과 | 판정 |
|---|---:|---|
| BTCUSDT USD-M 1m | 1,048,320봉 = 기간의 매분 | 준비 |
| BTCUSDT USD-M 5m | 209,664봉 = 기간의 매 5분 | 준비 |
| BTCUSDT USD-M 1h | 17,472봉 = 기간의 매시간 | 준비 |
| BTCUSDT funding, 2021~2022 | 2,190건, 최대 간격 8시간+47ms | 준비 |
| `macro_events` | 0건 → **101건 적재 (2026-09-23, [05](05-a1-macro-pit-source-audit.md) 매니페스트)** | 적재 완료, 실행 스크립트 연결 전 |

가격봉 수는 해당 기간의 기대 그리드와 일치한다. funding 시각에는 수십 ms의
지터가 있으나 9시간 초과 공백은 없다. 이는 **입력 존재** 검사이지,
전체 시장 데이터 품질 게이트나 백테스트 성적의 통과를 뜻하지 않는다.

## 왜 현재 TRAIN을 실행할 수 없는가

`config/base.yaml`의 G6는 FOMC·CPI·PCE·NFP·POWELL 발표 전후의 신규 진입을
막는다. 기존 `RiskGateEngine`은 `event_calendar_available=False`이면
`EVENT_BLACKOUT`으로 진입을 거부한다. 현재 `macro_events`가 비어 있고,
`BacktestEngine`에는 감사된 `MacroCalendar`를 주입해 시점별 공개 판본으로
블랙아웃을 계산하는 경로를 연결했다. DB의 일정 판본을 인과적으로 읽는
어댑터도 추가했지만 [05 원천 감사](05-a1-macro-pit-source-audit.md)의
완전성 증빙은 없다. 따라서 현재 실데이터에는 달력을
주입할 수 없고 A1 진입은 계속 거부된다.

여기서 `event_calendar_available=True`를 고정 주입하면 **없는 일정을 있는 것처럼
가장**하고 모든 발표 창을 통과시킨다. 0건 거래를 A1 전략의 실패로 해석하는 것도
틀리다. 둘 다 하지 않는다. 현재 상태는 `DATA_INSUFFICIENT / 검증 중단`이다.

## TRAIN 전에 필요한 작업

공식 자료의 시작점은
[Fed FOMC 일정·회의 기록](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm),
[BLS 2021 발표 일정](https://www.bls.gov/schedule/2021/) 및
[고용보고서 보관본](https://www.bls.gov/bls/news-release/empsit.htm),
[BEA 2021 발표 일정 보관본](https://www.bea.gov/system/files/2020-12/2021%20News%20Release%20Schedule_0.pdf),
[Fed Powell 연설 보관본](https://www.federalreserve.gov/newsevents/speech/powell20220826a.htm)이다.
이 링크들은 **가능한 원천**일 뿐 완전한 시점별 일정 데이터가 아니다.
특히 연설의 실제 개최일과 그 일정이 **언제 공개됐는지**는 별도로 검증해야 한다.

1. 2021~2022년 위 5종 일정의 **당시 공개 시각**(`vintage_ms`)과
   `scheduled_ms`를 출처별로 수집·검증한다. 후대에 수정된 최종 일정만으로
   과거에 미리 알 수 있었다고 가정하면 look-ahead가 된다.
2. 연속 기간·종류별 누락 검사를 통과한 경우에만 `CalendarCoverage`를
   만들 수 있다. 엔진은 해당 범위와 종류를 벗어나면 fail-closed한다.
   이벤트 수만 보고 coverage를 만들거나 합성 테스트 coverage를 실데이터에
   재사용해서는 안 된다. DB 읽기 어댑터는 추가됐지만 독립 감사 도구와
   원천 데이터는 아직 없다.
3. 시점별 공개 판본 선택, 발표 직전·직후 경계, 감사 공백, 감사되지 않은 빈 일정의
   fail-closed 단위·백테스트 통합 테스트는 추가했다. 실제 과거 일정 수집 후
   전체 기간 경계와 DB 어댑터 통합 테스트가 더 필요하다.
4. 같은 `decide()`/Risk 경로로 주 arm·대조군을 실행할 준비를 마친 뒤,
   사전등록한 TRAIN을 **한 번만** 실행한다.

G6 한도를 완화하거나 이벤트 종류를 삭제하는 것은 이 감사의 대안이 아니다.
