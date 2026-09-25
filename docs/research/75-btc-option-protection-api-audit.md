# 75 — Binance BTC 옵션 보호·청산 API 재확인

작성: 2026-09-26 · Phase 0. [74의 실행 안전성 게이트](74-btc-option-execution-safety-gate.md)에서 남긴 “공식 옵션 보호·청산 기능”을 공개 문서로 재확인한다. [TRADING_CORE](../current/TRADING_CORE.md)의 첫 체결 보호·보호 공백 5,000ms·실패 시 시장가 전량 청산 규칙을 **옵션에 자동 적용할 수 있는지** 판단하는 작업이지, 규칙 변경이나 주문 설계 승인이 아니다. 계정 인증·주문·유료 구매·기존 운영/코드 변경은 없다.

## 판정

`DOCUMENTED_OPTION_STOP_NOT_FOUND / ORDER_CANCEL_IS_NOT_POSITION_CLOSE / REDUCE_ONLY_LIMIT_NOT_GUARANTEED / POLICY_DECISION_REQUIRED`.

2026-09-26 공개된 [Binance 옵션 REST Trade 명세](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/trade)의 신규 주문 `/eapi/v1/order`와 일괄 주문 `/eapi/v1/batchOrders`에서 `type`은 **`LIMIT`만 열거**한다. `timeInForce`의 IOC/FOK, `reduceOnly`는 있지만 `STOP_MARKET`·`stopPrice`·`closePosition` 또는 `MARKET` 주문 유형은 이 명세에 없다. 일괄 주문은 동시 처리하되 매칭 순서를 보장하지 않는다. 이는 **검토한 공개 옵션 주문 인터페이스에서 동등한 기능을 찾지 못했다**는 의미이며, Binance 전체 기능의 영구적 부재나 모든 계정/상품의 동일성을 증명하지 않는다. 현행 선물용 보호주문을 옵션에 복사하거나 현물·선물의 TP/SL/OCO 설명을 옵션 API 증거로 전용하지 않는다.

## 기능별 대조

| 공개 기능 | 확인된 동작 | 현행 보호 규칙과의 관계 |
|---|---|---|
| 옵션 신규 주문 `POST /eapi/v1/order` | `LIMIT`, GTC/IOC/FOK/GTX, `reduceOnly`, ACK/RESULT. `executedQty`를 반환할 수 있음 | 숏 옵션 매수 청산을 **시도**할 수는 있으나 거래소 측 조건부 스톱이 아니며, IOC/FOK도 거래 상대방·전량 체결·5초 이내 완료를 보증하지 않음 |
| 일괄 주문 `POST /eapi/v1/batchOrders` | 다중 주문 동시 처리, 매칭 순서 미보장 | 보호 롱 선체결을 보증할 수 없으므로 원자적 스프레드 보호가 아님 |
| 개별·심볼·기초자산별 주문 취소 `DELETE` | **열린 주문** 취소 | 체결된 숏 포지션을 닫지 않음. 취소 응답 뒤에도 체결/포지션 대조가 필요 |
| [옵션 auto-cancel kill-switch](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/market-maker-endpoints) | heartbeat가 없으면 열린 주문을 취소하고 새 주문을 거부. 최소 countdown 5,000ms | 작동 대상을 포지션이 아니라 **미체결 주문**으로 명시. 5,000ms 숫자가 같아도 현행 “5초 안에 보호하거나 전량 청산”과 동등하지 않음. 이미 체결된 숏은 남을 수 있음 |
| [Market Maker Protection](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/market-maker-endpoints) | 단시간 대량 체결 문턱에서 MMP 주문 취소·신규 MMP 주문 거부 | 가격 손절·기존 포지션 청산 기능 아님. 일반 계정 이용 가능성도 이번에 확인하지 않음 |
| 거래소 상태 조회 | 주문 단건/열린 주문·옵션 포지션 조회, [`GET /eapi/v1/marginAccount`](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/account)로 옵션 지갑 `equity`·`available`·`initialMargin`·`maintMargin`·`adjustedEquity` 등을 제공 | 대조에 필요한 *인터페이스는 있음*. 모두 서명 요청이며 계정별 값·시점 일관성·조회 지연은 미검증. 조회 가능성이 즉시 청산 보증은 아님 |

`reduceOnly=true`는 반대방향 새 포지션 방지에 필요한 속성이지만, **체결 보증 속성이 아니다**. 예컨대 숏 옵션을 닫으려 `BUY LIMIT IOC`를 내도 표시 ask가 사라지거나 수량이 부족하면 잔여 숏이 남을 수 있다. 더 높은 가격 한도로 다시 시도해도 실제 손실과 시간 상한을 보장할 수 없다. `clientOrderId`도 공개 명세상 **미체결 주문에서만 중복 금지**이므로, 재시작 후 거래소 중복 거부를 영구 멱등성으로 쓰지 않는다. 이 해석은 API 필드와 [73의 무료 호가 관측](73-btc-option-two-leg-quote-overlap.md)을 결합한 설계상 추론이지 실계좌 체결 실험 결과가 아니다.

## 위험 규칙 충돌의 최종 분류

1. **확인된 실패 경로:** 일괄 주문으로 숏이 보호 롱보다 먼저 체결될 수 있다. kill-switch/MMP/주문 취소는 그 숏 포지션을 청산하지 않는다. 따라서 이 조합은 거래당 1% 위험과 첫 체결 보호의 증거가 될 수 없다.
2. **순차 롱 선체결의 조건부 가능성:** 거래소가 확인한 동일 만기·유형·단위의 보호 롱 수량 이하로 숏 주문을 제한하면, *만기 페이오프*의 단독 숏 구간은 피할 수 있다. 하지만 보호 롱이 현행 **거래소 측 STOP_MARKET**을 대체한다고 승인된 규칙은 없다. 독립 숏 증거금, 중간 강제청산, 계약/수량 불일치, 옵션 지갑 부족을 만기 손실 상한으로 지울 수 없다([Binance 옵션 매도·증거금 FAQ](https://www.binance.com/en/support/faq/detail/1ceb77f837344e3085ecf98d9a6a8097)).
3. **현행 규칙을 그대로 둘 때 미증명:** 옵션 문서화 주문 유형만으로 보호 등록 실패 후 **3초 내 재시도 및 보호 공백 5초 초과 시 시장가 전량 청산 전송·상태 대조**를 구현할 수 있다는 증거가 없다. 현행 규칙이 체결 완료를 5초 안에 보증한다는 뜻은 아니다. 다만 `LIMIT` 축소 주문의 전송은 규칙이 요구하는 **시장가 비상 청산 경로**와 다르다. 따라서 이 증거가 없는 옵션 매도 실거래는 계속 차단한다.
4. **알 수 없는 것:** 사용자 계정의 옵션 long/short 모드·관할 자격·실제 적용 계약/수수료·지갑 증거금·현행 옵션 보호 대안/특수 프로그램 접근 여부는 `ACCOUNT_UNVERIFIED`. 서명 API 호출이나 주문은 하지 않았다. 미확인을 “기능 없음”으로 단정하지 않는다.

## 사용자 결정이 필요한 경계

현행 [TRADING_CORE](../current/TRADING_CORE.md)의 선물형 보호주문·5초 비상 청산 규칙은 그대로 유지한다. **기본 선택은 옵션 매도 연구의 실거래 연결 중단**이며 Paper/데모에도 옵션 주문 경로를 추가하지 않는다. 옵션 매도 트랙을 계속할 경우, 먼저 사용자가 “거래소에 먼저 체결·유지된 보호 롱을 옵션의 별도 보호 방식으로 인정할지”를 결정해야 한다. 그 선택도 1% 위험·10% 증거금·1.5배 잔고 한도를 면제하지 않으며, 중간 강제청산과 부분 체결 때 위험을 제한할 별도 명세·모의 장애 시험·계정 읽기 검증이 필요하다. 인정하지 않는다면 현재 공개 API 근거로는 **옵션 매도 실거래의 현행 보호 규칙 통과를 주장할 수 없다**.

이는 전략 수익성 판정이 아니다. 새 전략 사전등록·백테스트·매수 전용 옵션으로의 자동 전환도 하지 않는다. 기존 Phase 6 Paper와 10/23·10/24 점검은 변하지 않는다.
