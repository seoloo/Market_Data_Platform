# 실적 개선형 주도주 — 무료 역사 기준점·종료 증권 제한 감사

작성: 2026-10-05 (Codex). 기준 코드 `a91917d`. 이전: [136](136-fundamental-universe-accounting-gates.md).
**Phase 0 / REAL_SAMPLE_AUDITED / DATA_NOT_READY 유지. 전략 성과는 NOT_TESTED.**
사용자 재개 요청으로 Codex 전용 연구를 이어갔다. 기존 전략·계좌·주문·일정·UI·골든 불변.

## 1. 이번에 실제로 진전된 것

무료 SEC 원문에서 **2022-09-30 SPY 보유명세 전체 505행**을 찾았다.
중복 이름 0, 각 행 평가액을 Decimal로 더한 **330,650,343,579 USD**가 공시 보통주 합계와 일치했다.
현재 생존 종목만 소급하는 것과 달리 Twitter·SVB·Citrix도 남아 있어 역사 복원의 검산 기준점으로 유용하다.
다만 ETF 보유명세는 공식 지수 구성 명부도, 당시 매매 가능 종목 목록도 아니다.
505행은 증권 행 수이며 기업 수·독립 관찰 수·검정력 표본 수가 아니다.

자료 게이트를 통과했다고 주장하지 않는다. 다음 세 경계가 실제 사례로 확인됐다.

| 경계 | 확인 | 연구 사용 제한 |
|---|---|---|
| 보고 기준일 vs 공시 시각 | 9/30 보유명세가 SEC에 11/28 접수 | 9/30 의사결정에 이 공시를 이미 알고 있었다고 가정 금지 |
| ETF 보유 vs 거래 가능성 | Citrix가 9/30 명세에 있으나 마지막 거래는 9/29 | 보유행을 매매 가능한 주식/종가 체결로 자동 변환 금지 |
| 종료 법적 권리 vs 현금 가용 | Twitter 주당 54.20 USD 권리와 계좌 입금 시점은 별개 | 거래정지일·지수 퇴출일·마지막 종가를 현금 정산으로 대체 금지 |

## 2. 기준점의 출처·시각·완전성

- [SEC SPY 연차보고서](https://www.sec.gov/Archives/edgar/data/884394/000119312522293237/d309469dn30d.htm): Schedule of Investments의 Common Stocks만 추출한다. 뒤의 affiliate 보유 표·공정가치 표를 다시 합산하지 않는다.
- [접수 색인](https://www.sec.gov/Archives/edgar/data/884394/000119312522293237/0001193125-22-293237-index.htm): accession `0001193125-22-293237`, period `2022-09-30`, filing `2022-11-28`, accepted 표시 `2022-11-28 13:58:01`.
- 접수 표시 문자열에 임의 `Z`를 붙이지 않았다. 역사 UTC 가용 시각과 실제 연구 시스템 first-seen은 미인증이다. 이번 UTC 수집 ms는 2026년 실제 취득 시각만 뜻한다.
- 원본 합계 일치는 **표 추출 검산**이다. 공식 S&P 명부와의 완전한 대조, CIK/주식 종류/증권 ID 연결, 시장 전체 커버리지 인증은 아니다.
- ETF를 대리 유니버스로 채택하는 연구 변경도 하지 않았다. 과거 상태를 복원하는 증거와 당시 공개 정보로 사용하는 입력을 구분한다.

표의 첫 행 3M은 6,052,278주/668,776,719 USD, 마지막 Zoetis는 5,149,507주/763,620,393 USD다.
Twitter는 7,374,625주/323,303,560 USD, Citrix는 1,329,577주/138,276,008 USD다.
이 수치는 펀드 평가 금액이며 개인 주문 체결·개인 수익이 아니다.
공정가치 표의 Level 2 총액도 138,276,008 USD다. Citrix 행과 숫자가 같다는 대조만 보존하며,
행별 Level 분류가 자동 인증됐다고 주장하지 않는다. 전 행이 거래소 종가로 평가됐다는 가정은 금지한다.

## 3. 변경 체인: 무료 공식 사건은 연결 가능, 전 구간 완전성은 미확인

[S&P 2022-09-23 공고](https://press.spglobal.com/2022-09-23-PG-E-and-EQT-Set-to-Join-S-P-500-ExlService-to-Join-S-P-MidCap-400-Others-to-Join-S-P-SmallCap-600)는
**10/03 장전** S&P 500의 CTXS→PCG, DRE→EQT와 400의 EQT→EXLS를 함께 명시한다.
이는 500+400 합집합에서 지수 내부 이동과 신규 편입을 다르게 처리해야 함을 보여준다.
136의 10/27 TWTR→ACGL 공고와 함께 제한 사건 연결 경로를 확인했다.
9/30~11/28 사이의 모든 공고/정정/주식 종류 변경을 빠짐없이 찾았다는 증거는 아직 없다.
이 두 사건만으로 기준 명부를 오늘까지 재생하거나, 수량이 맞는다는 이유로 완전성을 승인하지 않는다.

### Citrix: 종료 권리가 보유명세에 남는 경우

[Nasdaq 공식 사건 공고](https://www.nasdaqtrader.com/TraderNews.aspx?id=eca2022-262)는
CTXS/CUSIP `177376100`, 마지막 거래일 **9/29**, 당일 **20:00 ET 거래 중단**,
**9/30 합병 완료**, **10/03 거래 suspension**, 주당 **104.00 USD 현금 대가**를 설명한다.
보유 평가액은 `1,329,577 × 104 = 138,276,008 USD`로 정확히 일치한다.
9/30에 지정가 주문이 체결됐다는 의미가 아니라, 정지된 증권의 평가/권리가 명세에 남을 수 있다는 증거다.

해당 페이지 머리 날짜는 9/26이지만 본문은 9/30 완료로 갱신되어 있다.
현재 수정본의 완료 정보를 9/26부터 알고 있었다고 역입력하지 않는다.
원본 버전별 최초 공개 시각과 실제 현금 지급/가용일은 미확인이다.
공고는 사건 근거이지 일별 OHLCV·정산 완료·총수익 시계열이 아니다.

## 4. Twitter: 네 사건 날짜와 실제 수령을 분리

[NYSE Form 25 첨부 공고](https://www.sec.gov/Archives/edgar/data/876661/000087666122000890/ruleprovisionnotice.htm)와
[접수 색인](https://www.sec.gov/Archives/edgar/data/1418091/000087666122000890/0000876661-22-000890-index.htm)을 새로 보존했다.
주체 기업은 Twitter CIK `1418091`, 제출 거래소는 NYSE CIK `876661`이다.
기업 CIK와 제출자 CIK를 혼동하지 않는다.

| 사건 | 원문상 날짜/시점 | 해석 |
|---|---|---|
| 합병 효력 | 2022-10-27 | 주식의 대체 현금 권리 발생 근거 |
| 거래정지 | 2022-10-28 장 시작 전 | 이때부터 정상 매매 가능하다고 가정 금지 |
| S&P 500 교체 | 2022-11-01 장 시작 전 | 136의 지수 사건, 거래정지와 다름 |
| NYSE 제거 예정 | 2022-11-08 영업 시작 | Form 25 첨부의 제거 의사 통지; 실제 완료 별도 인증 아님 |
| 개인 계좌 현금 수령/재투자 가능 | 미확인 | 위 날짜 중 하나로 채우지 않음 |

Form 25 색인의 `Effectiveness Date=2022-10-28`은 첨부의 제거 예정일 11/08이나 최종 거래일을 대신하지 않는다.
색인 accepted 표시 `2022-10-28 08:31:19`도 임의 UTC 변환하지 않았다.
일반 주당 현금 권리 **54.20 USD**는 무이자·해당 원천세 차감 등 원문 조건을 따른다.
136의 8-K 예외 조건도 보존하며 전 주식 종류의 일률 지급을 가정하지 않는다.
법적 권리·채권 평가·현금 수령을 분리한 회계 구현은 아직 없고, 종료 상태는 계속 **TERMINAL_SETTLEMENT_UNVERIFIED**다.

이번에는 검증된 종료 종목 일별 가격 파일/정산 계좌 자료를 확보하지 못했다.
비공식 가격 페이지를 정식 가격 원본으로 채택하거나, ETF 평가액÷수량을 OHLCV/체결가로 만들지 않았다.
어떤 무료 가격원도 존재하지 않는다는 결론은 아니다. 제한 감사 범위에서 총수익 자료가 미완성이라는 판단이다.

## 5. 재현과 보존

새 원본 HTML **6개, 1,315,929 bytes(약 1.26 MiB)**. 기존 20개 삭제/덮어쓰기 없음, 누적 26개.
[manifest](fundamental-historical-anchor-manifest.json)에 URL·원본 SHA256·실제 UTC 취득 ms·선택 검산 결과를 기록한다.
원본은 Git 제외 `data/research/fundamental_pilot/`에 보존한다. Git 복제만으로 전체 원본 검산은 재현되지 않는다.
SEC 식별 연락처는 승인된 SEC 요청에만 사용했고 저장 문서/manifest에는 넣지 않았다.
Nasdaq/S&P 요청에는 연락처를 보내지 않았다. 유료 구매·로그인·우회·계좌/주문 API 호출·예약 수집 없음.
SEC 색인의 제출 파일 크기와 내려받은 응답 bytes가 다를 수 있으므로 실제 보관 bytes의 해시를 고정한다.

아래는 이 **고정 HTML 표본만** 다시 검산하는 읽기 전용 PowerShell 코드다.
일반 HTML 파서나 전략 구현이 아니며 다른 제출본으로 범용 사용하지 않는다.
원본 누락/해시·합계·행수 변경은 중단한다. HTML을 실행하지 않고 정해진 표 구조를 읽는다.

```powershell
$auditManifest = Get-Content docs/research/fundamental-historical-anchor-manifest.json -Raw | ConvertFrom-Json
foreach ($auditSource in $auditManifest.sources) {
    $auditHash = (Get-FileHash -LiteralPath $auditSource.path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($auditHash -ne $auditSource.sha256) { throw "Source hash mismatch" }
}
$auditHtml = Get-Content data/research/fundamental_pilot/spy-20220930-n30d.html -Raw
$auditInSchedule = $false
$auditReachedTotal = $false
$auditHoldings = @()
foreach ($auditRow in [regex]::Matches($auditHtml, '(?is)<tr\b[^>]*>(.*?)</tr>')) {
    $auditCells = @([regex]::Matches($auditRow.Groups[1].Value, '(?is)<td\b[^>]*>(.*?)</td>') | ForEach-Object {
        ([System.Net.WebUtility]::HtmlDecode([regex]::Replace($_.Groups[1].Value, '<[^>]+>', ' ')) -replace '\s+', ' ').Trim()
    })
    if ($auditCells.Count -eq 3 -and $auditCells[0] -eq 'Common Stocks' -and $auditCells[1] -eq 'Shares' -and $auditCells[2] -eq 'Value') {
        $auditInSchedule = $true
        continue
    }
    if ($auditInSchedule -and $auditCells.Count -eq 3 -and $auditCells[0] -like 'Total Common Stocks*') {
        if ($auditCells[2] -ne '$330,650,343,579') { throw 'Unexpected reported total' }
        $auditReachedTotal = $true
        break
    }
    if (-not $auditInSchedule -or $auditCells.Count -eq 0) { continue }
    if ($auditCells.Count -ne 3 -or $auditCells[1] -notmatch '^\d[\d,]*$' -or $auditCells[2] -notmatch '^\$?\s*\d[\d,]*$') {
        throw 'Unexpected row inside holdings schedule'
    }
    $auditHoldings += [pscustomobject]@{
        name = $auditCells[0]
        shares = [decimal]($auditCells[1] -replace ',', '')
        value_usd = [decimal]($auditCells[2] -replace '[\$,\s]', '')
    }
}
[decimal]$auditSum = 0
foreach ($auditHolding in $auditHoldings) { $auditSum += $auditHolding.value_usd }
if (-not $auditReachedTotal -or $auditHoldings.Count -ne 505 -or @($auditHoldings.name | Sort-Object -Unique).Count -ne 505 -or $auditSum -ne [decimal]330650343579) {
    throw 'Holdings reconciliation failed'
}
[pscustomobject]@{rows=$auditHoldings.Count;sum_usd=$auditSum;strategy_result='NOT_TESTED'}
```

검증: 새 원본 **6/6** SHA256/크기, 기존 원본 **20/20** 해시 불변, 위 코드의 전체 **505행** 합계와
manifest 수동 **5행** 일치. 정상 재현 + 잘못된 해시/행수/합계의 거부를 원자료 변경 없이 확인했다.
초기 수동 표본 비교는 호출 자식 scope의 변수를 읽으려다 실패했고 동일 scope에서 재실행해 통과했다.
최초 SEC 다운로드 시도는 잘못된 User-Agent 구문으로 요청 전에 실패했고 정상 구문으로 6개 HTTP 200 취득했다.
전체 로컬 pytest **2,633건**, leakage **88건**, strict mypy **419파일**, ruff check/format **584파일**,
import 계약 **5/5** 통과. Python은 초기 제한 환경에서 프로세스 생성이 실패해 같은 가상환경 검증을 권한 확장으로 실행했다.
문서 링크·JSON·연락처 비보관·변경 범위 검사는 별도 수행했다. src/tests/config/CI 변경 없음.

## 6. 다음 연구 선택 — 성과 열람 전

무료 과거 복원은 일부 공식 원자료가 있어 가능성을 더 조사할 근거가 생겼다.
하지만 이 단계의 표 추출만으로 가격·실적을 붙여 순위를 만들거나 수익률을 시험하면 안 된다.

| 경로 | 이점 | 시간·표본 위험 / 승인 경계 |
|---|---|---|
| 과거 복원 계속 | 수년 구간을 확보하면 전향 대기보다 표본을 빨리 모을 가능성 | 공식 명부 연결·종료 가격/정산·회계 범위 검토에 추가 작업 필요, 완료 시간 미측정. 독립 표본/OOS 보장 아님 |
| 앞으로 동결 관찰 | 실제 수신 시각·공시 버전을 그때부터 기록 가능 | 과거 종료 표본 결함은 없어지지 않음. 분기 관찰이 쌓이는 데 여러 분기 이상 걸릴 수 있고 독립성/효과에 따라 검정력 부족 가능 |

권고는 **과거 복원의 핵심 공백을 한 번 더 좁혀 확인한 뒤 선택**하는 것이다.
다음 Phase 0 유한 범위: TWTR/CTXS 중 한 사례의 무료 가격 원본과 현금 전환 회계 경로를 검토하고,
확보 가능 항목/불가 항목을 가격 공급자·증권 식별자·시각·배당/분할별로 표로 남긴다.
미확인 현금 가용일을 임의 거래일로 채우지 않는다. 이어 기준점→변경 체인의 누락 검출 계약을 설계한다.
자동 전체 수집·새 관찰 예약·ETF 대리 유니버스 채택은 이번에 시작하지 않았다.
가설·효과·비용·기간별 커버리지·80% 검정력·미열람 평가 계획이 확정되기 전 사전등록/전략 구현 없음.
