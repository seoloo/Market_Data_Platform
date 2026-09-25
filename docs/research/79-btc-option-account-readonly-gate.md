# 79 — BTC 옵션 계정 읽기 전용 자격·최소 담보 점검

작성: 2026-09-26 · Phase 0 · [78의 B/C 계좌·청산 게이트](78-btc-option-long-first-offline-check.md) 후속. 사용자의 “그래 해봐”를 **읽기 전용 계정 점검 승인**으로 적용했다. 주문·이체·계정 설정·권한 변경·유료 구매는 하지 않았다. 개인 잔고·포지션·비밀값은 문서에 저장하지 않는다.

## 직접 확인한 범위

기존 프로젝트 `.env`의 키를 값 출력 없이 로드했다. Binance의 API 제한 조회는 읽기 권한과 IP 제한이 켜져 있고, 옵션·현물·선물 거래 및 출금 권한은 꺼진 상태임을 반환했다. 이 권한 상태를 유지한 채 [공식 옵션 계정 조회 `GET /eapi/v1/marginAccount`](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/account)를 **1회** 서명 요청했다. 응답은 HTTP 401, Binance 코드 `-2015`(API 키·IP·권한 중 하나의 문제 가능)였다. 계정의 `canTrade`, equity, available, initial/maintenance margin 등은 받지 못했다. 같은 키로 옵션 포지션·열린 주문 등 다른 비공개 엔드포인트를 반복 호출하지 않았다.

`-2015`만으로 옵션 계정의 존재·관할 자격·매도 모드 부재를 판정할 수 없다. 특히 옵션 거래 권한이 꺼진 **안전한 읽기 전용 키**라는 사실과, 이 키로 옵션 계정 조회가 허용된다는 것은 별개다. 이 점검을 위해 옵션 거래 권한을 켜거나 새 키의 권한을 확대하지 않는다.

## 공개 계약 규격으로만 계산한 하한

2026-09-25 22:37 UTC 부근 [옵션 공개 `exchangeInfo`와 BTC 지수](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/market-data)를 읽었다. 거래 중인 BTCUSDT 옵션 668계약에서 가장 작은 `minQty × unit`은 **0.01 BTC**, 지수는 **84,050.24152174 USDT/BTC**였다. `nakedSell=true`로 표시된 계약은 0개였다. 공개 `nakedSell` 표기만으로 **이 계정의** 옵션 매도 자격을 부정하지 않는다. [Binance 매도 안내](https://www.binance.com/en/support/faq/detail/1ceb77f837344e3085ecf98d9a6a8097)는 BTC·ETH 매도 기능과 별도 long/short 모드를 설명하므로, 공개 필드와 계정별 적용 범위는 별도 확인 대상이다.

[Binance의 숏 포지션 초기증거금 식](https://www.binance.com/en/support/faq/detail/1ceb77f837344e3085ecf98d9a6a8097)에서 `max(지수×10%, 지수×15%−OTM)`의 **절대 하한**만 최소 수량에 적용하면 `84,050.24152174 × 0.01 × 10% = 84.05024152174 USDT`다. 따라서 현행 [증거금 ≤ equity 10%, 가용잔고 ≥ 필요 증거금 1.5배](../current/TRADING_CORE.md)의 **필요조건 하한**을 센트 단위로 올리면 숏 초기증거금 ≥ **84.06 USDT**, equity ≥ **840.51 USDT**, 옵션 지갑 available ≥ **126.08 USDT**다. 이는 마크 가격·주문 초기증거금·보호 롱 프리미엄·수수료·기존 포지션·ATM의 더 큰 증거금을 **제외한 가장 느슨한 하한**이다. 실제 계정이나 특정 콜/풋 쌍의 진입 허용값이 아니다. 현재 이 하한과 비교할 계정 수치는 없으며, 지수·상장 규격은 계속 변한다.

## 판정과 다음 경계

`ACCOUNT_READ_DENIED_-2015 / PUBLIC_MARGIN_FLOOR_ONLY / SHORT_ELIGIBILITY_UNVERIFIED / TIMED_EXIT_UNVERIFIED / NO_TRADING_APPLICATION`.

78의 A(오프라인 계약·수량 필요조건) 외에 B(실계좌 위험)와 C(시간 내 축소·청산)는 **통과하지 않았다**. 단지 계정 조회 권한 오류가 났다고 옵션 프리미엄의 경제적 우위를 기각하는 것도 아니다. 보호 롱 검사기·기존 Paper·주문 경로와 위험 정책은 그대로 둔다.

계속 확인하려면 거래 권한 확대 없이 옵션 계정 읽기만 허용되는 **별도 자격/키가 실제로 가능한지** Binance 설정·공식 지원에서 먼저 확인해야 한다. 가능하고 사용자가 제공/승인한 경우에만 새 읽기 전용·IP 제한 키로 계정·포지션·열린 주문을 다시 조회하고, 비밀값·원응답은 저장하지 않은 채 파생된 게이트 결과만 기록한다. 그렇지 않으면 B/C는 `UNVERIFIED`로 유지하고 옵션 매도 자동화 후보는 적용하지 않는다. 계정 읽기가 가능해지더라도 [75의 옵션 시장가·조건부 보호 미확인](75-btc-option-protection-api-audit.md)과 [77의 RFQ 자동화 미확인](77-btc-option-rfq-api-and-leg-contract.md)은 별도로 남는다.
