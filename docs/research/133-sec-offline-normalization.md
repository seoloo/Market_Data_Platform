# 133 — 실적 개선형 주도주: SEC JSON/PIT 정규화 연결

작성: 2026-10-04 (Codex). 이전: [131](131-fundamental-leaders-feasibility.md) · [132](132-fundamental-pit-audit.md).
단계: **Phase 0 유지**. 상태: **RAW_PARSER_READY / REAL_DATA_PENDING**.
실제 공시 소표본의 확보·회계 대조나 전략 성과 검증을 완료했다는 뜻이 아니다.
129·130 보류, 기존 전략·Paper·계좌·주문·예약·위험 한도는 그대로다.

## 1. 무엇이 달라졌는가

132는 정규화된 재무 숫자를 입력받아 계산하는 기반이었다.
이번에는 SEC 형태의 **companyfacts JSON → 공시 ID별 submissions 접수 시각 연결 →
원문/가용 시각 감사 입력 대조 → 132의 PeriodValue** 경로를 구현했다.

예: 5월에 알려진 분기 실적과 8월 정정 실적을 각각 보존한다.
6월 판단에는 5월 당시 가용값만 쓰며, 8월 정정 내용을 과거 판단에 끼워 넣지 않는다.
반대로 접수 시각이나 회계 비교 가능성 확인이 없으면 숫자가 있어도 계산 대상에서 제외한다.

모듈: [sec_fundamentals.py](../../src/auto_trading/research/sec_fundamentals.py).
테스트: [test_sec_fundamentals.py](../../tests/research/test_sec_fundamentals.py).
네트워크·파일 읽기/쓰기·시계·자동 수집·종목 점수·주문이 없는 연구 전용 함수다.
기존 독립 연구 import 계약에 추가하여 매매/실행/거래소/app 경로가 가져오지 못하게 했다.

## 2. 입력과 처리 계약

### 공시 자료

- `raw`: companyfacts 원본 bytes. 부동소수점 변환 없이 JSON 소수는 Decimal로 읽는다.
- `SubmissionSource(cik, raw)`: 현재 submissions의 `filings.recent`, 별도 과거 파일의 열 배열을 지원한다.
  과거 파일에 CIK가 없을 때는 **호출자가 제공한 수집 manifest의 CIK 연결**을 신뢰하는 경계다.
  이 파서가 CIK 없는 과거 파일의 소유 기업을 자체 인증하는 것은 아니다.
- 현재 파일의 `filings.files` 목록은 자동으로 내려받거나 읽지 않는다.
  필요한 과거 파일을 별도로 확보해 명시적으로 전달해야 한다.
- accessionNumber·form·filingDate 열 길이와 공시 ID 형식을 검사한다.
  중복 공시 메타데이터가 충돌하면 중단한다. 동일 공시의 누락 시각은 다른 제공 원자료의
  일치하는 명시적 접수 시각으로만 보완하며 추정값은 만들지 않는다.

### 원문 감사 입력

`FactReview`: 정확한 Concept(CIK/taxonomy/tag/unit)·공시 ID·기간 시작/끝·
available_at_ms·basis_id·reference를 지정한다.

같은 기업/연도/공시라는 이유로 모든 값에 회계 기준을 자동 배정하지 않는다.
동일 기준으로 합산/차감할 수 있는지는 원문 대조자가 확인한다.
reference 문자열 존재 검사는 **감사 증거가 진짜임을 증명하지 않는다**.
근거 원문/거래일 자료를 보관하고 사람이 검토하는 절차는 여전히 필요하다.

### 가용 시점과 범위

- 접수 시각은 `acceptanceDateTime`의 UTC 명시(`Z`, `+00:00`)만 지원한다.
  시간대 없는 시각/기타 오프셋은 추측하지 않고 중단한다. 실제 자료에 다른 형식이 나오면
  공식 의미와 원문을 확인한 뒤 지원 계약을 따로 확장해야 한다.
- 접수 시각 없는 과거 파일은 filingDate 자정으로 대체하지 않고 해당 값의 연결을 제외한다.
- available_at_ms는 외부 감사 입력이다. 접수 이전 가용값은 거부한다.
  **NYSE 달력·휴장·DST·보수적 다음 거래일 규칙 구현은 아직 없다.**
  합성 테스트의 가용 시각은 이 거래일 정책을 인증하지 않는다.
- 연구 대상 입력 형식은 표준 us-gaap/ifrs-full duration, 10-K·10-Q와 정정본이다.
  이 지원 범위가 최종 투자 유니버스 선정이나 IFRS/GAAP 상호 비교 가능성을 뜻하지 않는다.
  earnings release/8-K를 10-Q와 같은 자료로 자동 연결하지 않는다.
- fy/fp/frame을 기간 식별·가용 시각으로 사용하지 않는다. 정확한 start/end를 유지한다.
  다른 태그/통화로 결측을 자동 대체하지 않는다.

## 3. 결측·손상·출처

| 상황 | 처리 |
|---|---|
| 정확한 taxonomy/tag/unit 없음 | `concept_missing=True`, 빈 값. 다른 태그로 대체 안 함 |
| 접수 메타데이터/접수 시각 없음 | `ACCEPTANCE_NOT_VERIFIED` 제외 |
| 정확한 기간의 회계 기준/가용 시각 감사 없음 | `PERIOD_BASIS_AVAILABILITY_NOT_REVIEWED` 제외 |
| instant 값 | `INSTANT_FACT_NOT_DURATION` 제외 |
| 연구 지원 form 밖 | `FORM_NOT_IN_RESEARCH_SCOPE` 제외 |
| CIK·공시 form/filingDate 불일치, 열 길이 오류, JSON 중복 key/손상, 비정상 숫자 | DataIntegrityError로 중단 |
| 같은 공시/기간의 서로 다른 값 | 보존하되 `select_as_of`가 가용 시점의 충돌을 거부. 임의 한 값 선택 안 함 |
| 실제 금액 0 | Decimal(0)으로 보존. 결측으로 취급 안 함 |

원본 companyfacts bytes SHA256, 제공한 submissions bytes SHA256 목록과 사용한 review reference를 반환한다.
각 정규화 값은 원래 공시 ID와 접수/가용 시각을 갖고 계산 출처로 전파된다.
순서를 바꿔도 값/제외 내역은 동일하지만 원본 bytes가 다르면 SHA256은 다르다.

**해시는 무결성/재현성 표식이지 공시 공개 시점 증명이나 과거 revision 완전성 인증이 아니다.**
최신 companyfacts에서 빠진 과거 사실을 복원할 수는 없다. 실제 감사 때 수집 시각·원문 출처·
과거 제출본 대조·누락 범위를 별도 기록해야 한다. 해당 수집 manifest/CLI는 아직 구현하지 않았다.
행 수·접수 공시 수·고유 재무 기간 수·독립 통계 표본 수는 서로 다르다.
파서에서 값이 나온 것을 커버리지/검정력/수익성 게이트 통과로 취급하지 않는다.

## 4. 합성 검증

- 새 SEC 형태 입력 테스트 50건 + 기존 계산 38건 = **88건 통과**.
- 가용 전 공시 제외, 미래 정정 무관성(hypothesis), 현재/과거 JSON 연결, 기업/공시 불일치,
  회계 감사 결측·중복, 시간대 불명, 손상 JSON, Decimal 정밀도, 순서 불변을 검증했다.
- SEC 형태의 누적 현금흐름 10/25/40/60을 정규화 → 분기 10/15/15/20 → 최근 1년 **60**.
  원 공시 4개의 출처와 가장 늦은 가용 시각이 합계까지 전파된다.
  11월에 가용해지는 3분기 값은 10월 판단에 나타나지 않는다.
- 테스트 데이터는 전부 **가상 기업/가상 숫자/가상 감사 근거**다.
  실제 SEC 응답으로 지원 형식을 검증하거나 실제 기업 지표를 산출한 결과가 아니다.
- 대상 strict mypy 2파일 및 ruff check 통과.
- 전체 pytest **2,526건 통과**(177.83초), leakage **84건 통과**,
  전체 strict mypy **401파일**, ruff check/format **555파일**, import 계약 **5/5** 통과.
  기존 골든/회귀 기대값을 바꾸지 않았다. 기준 코드 f2f7e60, 작업 시작 시 작업 트리는 깨끗했다.

## 5. 외부 접근과 무료 자료 원칙

이번에는 SEC 공식 API 설명을 다시 확인했으며 JSON API에 추가 요청/우회/다운로드를 하지 않았다.
131에서 실제 공개 API 두 곳의 HTTP 403을 확인했고 원인은 여전히 미확정이다.
요청에 표시 가능한 연락처 답변 대기 상태를 유지한다. Git 이메일·계좌 정보·.env를
찾아 외부에 보내거나 임의의 연락처를 만들지 않았다. 유료 구매·벌크 ZIP 다운로드 없음.

공식 API는 현재/과거 submissions 분리와 표준 taxonomy/entity 범위, 갱신 지연을 설명한다.
이 설명은 본 프로젝트의 실제 접근 성공이나 회계/PIT 검증을 대신하지 않는다.

- [SEC API 공식 설명](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC 개발자 FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)

## 6. 다음 단계와 완료 조건

다음은 **Phase 0 — 실제 공시 소표본 감사**다. 다시 계산 기반을 만들거나 바로 백테스트하는 단계가 아니다.

1. 허용된 무료 공개 경로로 기업 하나의 companyfacts·현재/필요한 과거 submissions 및 대응 원문 확보.
   최초 접근 점검에 사용한 기업은 자료 구조 확인용일 뿐 수익률/종목 선정 표본이 아니다.
2. 매출·영업이익·현금흐름의 태그/통화/실제 회계 기간과 원문 숫자를 대조한다.
   YTD/Q4 차감의 기준 일치 여부, 사용하지 못하는 값의 이유를 기록한다.
3. 접수·실제 가용·보수적 거래일 시각을 근거와 함께 확인해 FactReview를 작성하고 파서에 연결한다.
   실제 정정 사례를 확보하지 못하면 해당 검증은 합성 수준이라고 명시한다.
4. 결과 값·출처·해시·제외 건수와 원문 검산의 일치를 감사한다.
   최근 JSON만으로 과거 revision 완전성을 증명했다고 주장하지 않는다.
5. 소표본 형식 감사 후 과거 유니버스/상장폐지/자료 커버리지/검정력을 확인한다.
   이들이 준비되기 전 수익성 사전등록·성과 실험·실거래 적용은 하지 않는다.

자료 접근이 거부되면 반복 우회하지 않고 수동 제공 가능성/공식 대안과 제한을 기록한다.
운영/전략 Phase 승격, 과거 연구 재개, 새 데이터 자동 수집 예약은 이번 작업에 포함하지 않았다.
