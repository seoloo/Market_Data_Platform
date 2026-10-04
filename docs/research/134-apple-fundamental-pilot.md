# 134 — 실적 개선형 주도주: Apple 실제 공시 소표본 감사

작성: 2026-10-04 (Codex). 기준 코드 a8ba226. 이전: [133](133-sec-offline-normalization.md).
단계: **Phase 0 유지**. 상태: **REAL_SAMPLE_AUDITED / DATA_NOT_READY**.
실제 기업 원자료의 숫자 연결 감사이지 투자 전략 PASS나 수익성 검증이 아니다.
Apple은 131 최초 접근 점검의 연속 표본이다. 수익률/매수 매력으로 선택하지 않았다.
129·130 보류, 기존 매매·Paper·계좌·예약·위험 한도는 변경하지 않았다.

## 1. 무료 원자료 확보

사용자가 SEC 요청에 표시할 연락처를 명시적으로 제공했다.
승인한 주소는 요청자 식별에만 사용했으며 코드·Git·보고서에는 기록하지 않았다.
최초 두 로컬 요청은 User-Agent 형식 검증에서 FormatException으로 실패했다.
헤더 형식을 바로잡은 실제 공개 JSON 두 요청은 **HTTP 200**이었다.
이전 131의 HTTP 403은 당시 관찰로 보존한다. 원인 확정/항상 접근 가능/연락처만 있으면 해결을 주장하지 않는다.
접근 제한 우회·계좌/주문 API·유료 구매·전체 벌크 ZIP 다운로드 없음.

원본 7개 **14,529,297 bytes(약 13.86 MiB)**는 `data/research/fundamental_pilot/`에 보관, Git 제외.
아래의 완전한 SHA256·URL·수집 시각(UTC epoch ms)은 감사 실행기의 `SOURCES`와 실행 보고서에 있다.
수집 시각은 2026년 현재의 관찰 시각이지 2024년 최초 공개 시각의 증거가 아니다.

| 원본 | 역할 | SHA256 앞 12자리 |
|---|---|---|
| apple-submissions.json | 기업/공시 ID/접수 시각 | 3cf0928a15c7 |
| apple-companyfacts.json | 표준 태그·USD·기간·금액 | 73a86c6aedc3 |
| apple-fy2024-10k.html | 연간 연결 재무표·XBRL context·회계 주석 | 28f9ca979ae6 |
| apple-fy2024-q3-10q.html | 9개월 연결 재무표·XBRL context | fe6ee5a8e435 |
| apple-fy2024-q4.pdf | Apple 공식 연간/4분기 재무표 교차 검산 | af9646018330 |
| apple-fy2024-q3.pdf | Apple 공식 9개월 재무표 교차 검산 | e435ff39122e |
| nyse-2024-calendar.pdf | 선택한 판단 시각의 거래일 수동 대조 | 02282e733512 |

SEC 공개 제출본:
[FY2024 10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm),
[FY2024 Q3 10-Q](https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/aapl-20240629.htm).
API [submissions](https://data.sec.gov/submissions/CIK0000320193.json)·
[companyfacts](https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json)는 최신 스냅숏이며 내용이 이후 바뀔 수 있다.

## 2. 실제 형식에서 발견한 문제

실제 `acceptanceDateTime`은 `.000Z` 밀리초 형식이었다.
133 파서는 초 단위만 허용해 실제 JSON 연결을 거부했다.
UTC 명시 원칙은 유지하고 **0~3자리 소수초**만 정확한 정수 ms로 읽도록 확장했다.
소수초 0자리란 소수점 자체가 없는 경우다. 시간대 없는 값/다른 오프셋/4자리 이상 소수초는 여전히 거부한다.
시각 계산에 부동소수점 timestamp를 쓰지 않는다. 기존 초 단위 의미·매매 규칙은 불변.

수집한 현재 submissions **1,001개 공시**가 파서에서 읽혔다.
이번 대상은 그중 아래 2개 제출본이다. 과거 별도 submissions 파일은 이번에 수집하지 않았다.

| 공시 ID | 접수 UTC | filingDate | 보고 기간 끝 |
|---|---|---|---|
| 0000320193-24-000081 | 2024-08-01 22:03:34 | 2024-08-02 | 2024-06-29 |
| 0000320193-24-000123 | 2024-11-01 10:01:36 | 2024-11-01 | 2024-09-28 |

10-Q의 filingDate를 UTC 자정으로 바꾸면 접수 시각과 달라진다.
접수와 filed를 같은 시각으로 취급하지 않으며 SEC가 제공한 각각의 필드를 보존한다.

<a id="manual-source-review"></a>

## 3. 원문 대조

PDF 읽기 스킬을 사용해 Apple 공식 PDF p1(손익)·p3(현금흐름)를 모두 렌더링하고
기간·열·단위·숫자를 눈으로 대조했다. SEC HTML의 연결 재무표와 dimension 없는 CIK context도 확인했다.
Q4 PDF p4의 non-GAAP 조정값은 선택하지 않았다. 원문 PDF는 다음과 같다.

:codex-file-citation{path="C:/Users/seolh/Documents/GitHub/Auto_Trading/data/research/fundamental_pilot/apple-fy2024-q4.pdf" purpose="source"}
:codex-file-citation{path="C:/Users/seolh/Documents/GitHub/Auto_Trading/data/research/fundamental_pilot/apple-fy2024-q3.pdf" purpose="source"}

선택 태그는 매출 `RevenueFromContractWithCustomerExcludingAssessedTax`, 영업이익 `OperatingIncomeLoss`,
영업현금흐름 `NetCashProvidedByUsedInOperatingActivities`, taxonomy `us-gaap`, unit `USD`다.
PDF 금액 단위는 **백만 USD**이므로 JSON USD와 1,000,000배 변환하여 비교했다.

| 기간 / 출처 | 매출(백만 USD) | 영업이익(백만 USD) | 영업현금흐름(백만 USD) |
|---|---:|---:|---:|
| FY2023: 2022-09-25~2023-09-30, **2024 10-K 비교열** | 383,285 | 114,301 | 110,543 |
| FY2024 9개월: 2023-10-01~2024-06-29, 2024 10-Q | 296,105 | 93,625 | 91,443 |
| FY2024 연간: 2023-10-01~2024-09-28, 2024 10-K | 391,035 | 123,216 | 118,254 |
| Q4 연간−9개월: 2024-06-30~2024-09-28 | 94,930 | 29,591 | 26,811 |

선택한 **9개 원 금액 모두** SEC JSON·원문·공식 PDF 대조에 일치했다.
Q4 매출/영업이익 차분은 Q4 PDF의 직접 분기 열과 일치한다.
Q4 현금흐름은 연간−9개월 **파생값**이며 PDF에 직접 분기 현금흐름 표가 있다고 주장하지 않는다.
출처 두 공시 ID와 가장 늦은 가용 가정 시각을 보존한다.

원문 Note 1/회계기간에 따라 FY2023은 **53주(371일)**, FY2024는 **52주(364일)**다.
12월 결산/달력 분기로 강제 변환하지 않는다. 총액 비교는 가능하지만 기간 길이 차이를 보정한 성장 지표가 아니다.
원문에는 일부 전기 금액 재분류 안내가 있으므로 이번 basis는 위 **연결 합계 9개**의 대조 범위에만 붙인다.
모든 계정·기업·과거 기간이 같은 기준이라는 자동 인증으로 확대하지 않는다.
특히 FY2023 값은 **2024 10-K에서 보고된 비교값**으로, 2023년 판단에 소급하지 않았다.

실행기 산술 산출: FY2024 매출 증가율 **2.021994%**, 영업이익률 **31.510223%**,
영업현금흐름/매출 **30.241283%**(표시 반올림).
이는 재무 산술이며 성장 가속·종목 점수·투자 수익률/초과수익 검증이 아니다.

## 4. PIT 판단 시각 — 가정과 사실의 분리

접수 시각은 실제 SEC 메타데이터다. **역사적 최초 배포/수신(first-seen) 시각은 미확보**다.
소표본 계산에는 131의 보수적 설계안에 따라 접수의 미국 동부 날짜 다음 거래일 종가를
수동 달력 대조한 **연구용 가용 가정**으로 썼다. 전략 사전등록/운영 적용 승인이나 실제 최초 공개 시각 인증이 아니다.

| 공시 | 동부 접수 날짜 | 이번 available_at_ms 가정의 UTC 표시 | 향후 거래 설계안의 다음 거래일 |
|---|---|---|---|
| Q3 10-Q | 2024-08-01 | 2024-08-02 20:00:00 (16:00 EDT) | 2024-08-05 |
| FY2024 10-K | 2024-11-01 | 2024-11-04 21:00:00 (16:00 EST) | 2024-11-05 |

[NYSE 2024 달력](https://www.nyse.com/publicdocs/ICE_NYSE_2024_Yearly_Trading_Calendar.pdf)의
해당 날짜·주말·정규/조기 폐장 구분을 시각 대조했고,
[NYSE 정규 거래시간](https://www.nyse.com/trade/hours-calendars) 및
[NIST DST 규칙](https://www.nist.gov/pml/time-and-frequency-division/local-time-faqs)을 확인했다.
2024-11-03 DST 종료를 넘겨 판단하므로 8월과 11월의 UTC 종가 시각이 다르다.
일반 거래일/DST 자동 달력 구현은 아직 없으며 이번 두 날짜 확인을 전체 기간 통과로 확대하지 않는다.

:codex-file-citation{path="C:/Users/seolh/Documents/GitHub/Auto_Trading/data/research/fundamental_pilot/nyse-2024-calendar.pdf" purpose="source"}

10월·가용 가정 이전의 연간 값 차단, 2023년에 2024 제출본 비교값이 보이지 않음을 검사했다.
실제 정정 제출본 비교는 이번 표본에서 수행하지 않았으며 133 합성 검증 수준으로 남아 있다.
현재 JSON/수동 지연만으로 역사적 revision 완전성·수집 지연을 보장하지 않는다.

## 5. 재현 실행과 제한

실행기 [audit_apple_fundamentals.py](../../scripts/audit_apple_fundamentals.py):

```powershell
.venv\Scripts\python.exe -m scripts.audit_apple_fundamentals --output reports/fundamental_pilot/apple-fy2024-audit-repeat.json
```

원본 7개 SHA256을 먼저 검사한 뒤 실제 전체 JSON을 연결해 검산한다.
결과 `reports/fundamental_pilot/apple-fy2024-audit.json`에는 원문 URL·수집 시각·파일 크기/해시·
실행 코드 해시·파생 숫자·제외 이유와 **historical_first_seen=NOT_VERIFIED / strategy_result=NOT_TESTED**를 보존한다.
실행기는 네트워크/계좌/주문을 호출하지 않고 원자료를 수정하지 않는다. 기존 결과 경로가 있으면 덮어쓰지 않고 실패한다.
원본이 없거나 최신 다운로드로 해시가 달라지면 중단한다. 해시 검사는 수동 원문 검토를 자동화한 것이 아니다.

대상 3태그의 원본 **485행** 중 검산 선택 9행, 회계/가용 미감사 336행,
접수 메타데이터 미연결 140행을 제외했다. 이 수치는 **한 기업의 제한된 선택 범위**이지
시장 전체 커버리지/독립 표본/전략 거래 수가 아니다. 현재 파일보다 오래된 공시 연결은 추가 감사 대상이다.

작은 [회귀 발췌 fixture](../../tests/fixtures/fundamental/apple-2024-excerpt.json)는
검토한 실제 9행과 2개 공시의 필드만 수동 발췌해 Git에 보관했다.
원본 전체 bytes와 같다고 주장하지 않으며 원본 SHA256과 발췌 파일 SHA256을 혼동하지 않는다.
온라인 재수집 없이 테스트할 수 있지만 실제 전체 스냅숏 감사는 위 원본 보관이 필요하다.

검증: 새 접수 형식 7건 + 발췌/파일·CLI 8건, 대상 파서/실행기 총 **65건 통과**.
전체 strict mypy **402파일**, ruff check/format **557파일**, import 계약 **5/5** 통과.
전체 pytest **2,541건 통과**(176.07초), leakage **85건 통과**(18.05초).
기존 골든/성과 기대값 변경 없음. 대상만 경로 지정한 초기 mypy 명령은 namespace/import 경로 오류였고,
프로젝트 표준 전체 strict mypy에서는 새 실행기 import를 포함해 통과했다.
src 검색 경로와 명시적 모듈을 지정한 실행기 별도 strict 검사 1파일도 통과했다. 타입 검사 기준을 완화하지 않았다.

## 6. 다음 단계

**Phase 0 — 다른 결산기 기업 소표본 및 과거 제출본/재분류/PIT 감사 확대.**
자료 구조·태그/회계 기간·과거 접수 연결·비교열 소급 방지를 먼저 확인한다.
그 뒤 당시 유니버스/상장폐지·기간별 재무 커버리지·독립 표본/검정력·미열람 분할을 확정한다.
가용 시각 가정/거래 달력도 성과 열람 전 명세에 고정하고 전향 수신 기록 계획을 정해야 한다.
이 과정 전 종목 랭킹·가중치·매수/매도·성과 시험·Paper 승격·실거래 적용을 하지 않는다.
