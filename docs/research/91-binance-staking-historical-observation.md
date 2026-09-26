# Binance WBETH·BNSOL 과거 환산비율 관찰 — 결과 열람 전 계산 계약

고정: 2026-09-26 · 상태: **기술통계 계획, 전략 사전등록 아님**. 이 문서의 수치·기간·출력 정의를 커밋한 뒤에만 결과를 계산한다. 사용자의 업비트 SOL 실제 보상, 실거래 또는 전략 PASS/REJECT로 해석하지 않는다. 관련 타당성 조사: [90](90-staking-eth-sol-feasibility.md).

## 질문과 상품 경계

ETH 또는 SOL **1개를 Binance 해당 상품으로 전환·보유·환매한다고 가정할 때**, WBETH:ETH 또는 BNSOL:SOL 환산비율 증가로 표시되는 기초 코인 수량 증가분은 얼마였는가? 동일 코인 1개 단순 보유의 코인 수량 증가 0과 비교하는 **설명적 지표**다. 코인 가격 변동, USDT/KRW 수익, 전체 자본 수익, 현물+선물 숏 손익, 청산 위험은 측정하지 않는다.

[WBETH 공식 설명][B1]은 환산비율이 보상 누적을 반영한다고 설명한다. [BNSOL 공식 설명][B2]도 같은 구조이며, 검증인 보상에 대한 상품 수수료는 보상에 반영된다. 추정 APR 필드·프로모션 boost/airdrop·별도 DeFi 보상은 환산비율과 합산하지 않는다. 과거 전 기간의 실제 접근 자격·전환/환매 대기와 비용을 확인한 것은 아니다.

[BNSOL 출시 공지][B3]는 2024-09-12 09:00 UTC에 게시되었다. 비율 이력은 2024-08-28부터 있어도 **출시 전 2024-08-28~09-12는 투자 가능한 백테스트 구간이 아니다**. 첫 온전한 UTC 날짜인 **2024-09-13 00:00 UTC**를 공통 시작점으로 고정한다. WBETH 역시 공식 소개가 2023-04-27 이후 보상을 설명하므로 초기 API 선행 행을 실행 가능 기간으로 사용하지 않는다.[B1] 종료점은 이미 수집한 자료의 고정 절단 **2026-08-31 00:00 UTC**다. 종료 당일 이후 보상은 계산하지 않는다.

## 데이터·검증·수식

- 소스: Binance 서명 GET `rateHistory`, Git 제외 `data/research/staking/manifest-1790416651513.json`이 지목하는 정규화 파일. ETH SHA-256 `f23fb91f79eb4c035f6f0f492e5298334a365b5892c99442a6c849bbf62623b4`, SOL SHA-256 `5e701cba2ca003c366659d724bc7630bc27c708d809d218c07199e5b615e008d`를 먼저 확인한다. 값은 아직 열람하지 않는다.
- UTC 00:00 하루 한 행, 중복·결측·음수/0/비유한 환산비율이 있으면 결과를 내지 않는다. 마지막 날짜가 없거나 파일 해시가 다르면 중단한다. `annualPercentageRate`는 결과 계산에 쓰지 않는다.
- 동일 공통 구간을 두 자산에 적용한다. `factor = exchangeRate(end) / exchangeRate(start)`; `기초코인 증가율(%) = (factor − 1) × 100`. 이는 정확한 비율로 전환·환매되고 보유 수량이 일정하다는 **가상 수량 산술**이다. 만약 감소하면 음수 그대로 보고한다.
- 전체 2024-09-13→2026-08-31의 연환산은 `(factor^(365 / 경과 UTC 일수) − 1) × 100`. 복리 지표이지 보장 APR이 아니다. Decimal 계산, 소수점 넷째 자리 반올림; 연환산은 **전체 구간만** 보고한다.
- 전체를 고정된 세 구간 `2024-09-13→2025-01-01`, `2025-01-01→2026-01-01`, `2026-01-01→2026-08-31`로 분해해 각 구간의 *단순 코인 수량 증가율*과 경과일을 표시한다. 구간 경계의 같은 행을 앞 구간 종료/다음 시작에 재사용하며 독립 표본 3개라고 주장하지 않는다.
- 두 자산의 숫자는 각자의 기초 코인 단위다. 숫자 크기로 투자 우열을 판정하지 않는다. 동일 기간 가격 위험·유동성·수수료·환매 조건이 다르다.

## 출력과 판정 금지

출력은 원본 스냅숏 체크섬, 고정 구간·경과일, 기초 코인 비율 증가율과 전체 연환산뿐이다. 원시 데이터·개인 계정값은 Git에 넣지 않는다. 결과 JSON은 Git 제외 `reports/research/staking/`에 저장하고, 집계만 이 문서의 후속 결과 절에 기록한다. **비용 후 계좌 수익/선물 헤지 성과/PASS·REJECT는 계산하거나 주장하지 않는다.** 이 관찰에는 거래 단위·효과 크기·SD·80% 검정력 게이트가 없어 전략 사전등록이 아니다. 이후 전략 연구가 필요하면 [BACKTEST_AND_TESTING](../current/BACKTEST_AND_TESTING.md) 게이트를 결과에 맞춰 낮추지 않고 새 미열람/전향 구간으로 별도 설계한다.

## 공식 근거

- [B1 — Binance WBETH 환산비율과 보상](https://www.binance.com/en-IN/support/faq/detail/e252366155174ba6887f6b32e3798273)
- [B2 — Binance SOL Staking FAQ](https://www.binance.com/en/earn-faq/dark/sol-staking/faq)
- [B3 — Binance BNSOL 출시 공지](https://www.binance.com/en/support/announcement/detail/de5969a9c20f42088fcf039101370e15)
- [Binance ETH `rateHistory` API](https://developers.binance.com/en/docs/catalog/investment-and-services-staking/api/rest-api/eth-staking)
- [Binance SOL `rateHistory` API](https://developers.binance.com/en/docs/catalog/investment-and-services-staking/api/rest-api/sol-staking)
