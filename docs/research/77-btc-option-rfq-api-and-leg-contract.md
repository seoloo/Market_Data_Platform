# 77 — BTC 옵션 RFQ 다중 다리 자동화·체결 계약 공개 근거 감사

작성: 2026-09-26 · Phase 0 · [76 보호 롱 선체결 연구](76-btc-option-long-first-protection-study.md)의 후속. 공개 Binance 문서만 검토했다. 계정 인증·주문·유료 데이터·코드·운영 설정 변경은 하지 않았다.

## 판정

`RFQ_MULTI_LEG_UI_CONFIRMED / PUBLIC_MULTI_LEG_API_NOT_DOCUMENTED / ATOMIC_FILL_CONTRACT_UNVERIFIED / ACCOUNT_ELIGIBILITY_UNKNOWN / LIVE_BLOCKED`.

RFQ의 다중 다리 **단일 주문 구성**은 공식 안내에 있다. 그러나 단일 주문이라는 표현만으로 두 다리의 전량 동시 체결, 부분 체결 불가, 실패 시 원복을 계약상 보장한다고 말할 수 없다. 이번에 확인한 공개 개발자 명세에는 이 RFQ 다중 다리를 자동 제출·조회할 수 있는 별도 공식 API가 나타나지 않았다. 이는 **그런 API가 어디에도 없다는 단정이 아니라, 현재 프로젝트에서 신뢰할 수 있는 공개 계약을 확보하지 못했다는 판정**이다. 따라서 RFQ를 현행 자동매매 실행 경로로 채택하지 않는다.

## 채널별로 확인한 것과 확인하지 못한 것

| 채널 | 공개 근거로 확인한 범위 | 자동화·두 다리 보장에 관한 한계 |
|---|---|---|
| Options RFQ 플랫폼 | [FAQ](https://www.binance.com/en/support/faq/detail/6d3a80c6574f482eb45457eac64bbff6) §14~15는 화면에서 견적 요청 후 Buy/Sell로 수락하는 흐름과 전략을 통한 다중 다리 거래를 설명한다. [출시 공지](https://www.binance.com/en/support/announcement/detail/ea4d4b4fa9f943fabd891c4d5836d230)는 여러 다리를 단일 주문·결합 가격으로 처리한다고 소개한다. | FAQ·공지에 RFQ 전용 공식 주문 API, 정확한 all-or-none/부분 체결/실패·원복 계약은 없다. 공지의 “위험 최소화”는 위험 제거 보증이 아니다. |
| 일반 Options 주문장 API | [Trade 명세](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/trade)의 `POST /eapi/v1/batchOrders`는 여러 주문을 병렬 처리하며 매칭 순서는 보장하지 않는다고 명시한다. | RFQ 단일 주문과 다른 채널. 롱·숏 두 주문을 이 API에 함께 넣어 원자적 수직 스프레드라고 취급할 수 없다. |
| Market Maker Block Trade API | [명세](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/market-maker-block-trade)의 `POST /eapi/v1/block/order/create`에 `legs` 배열이 있다. | **`Max 1 (only single leg supported)`**가 명시되어 있어 이 엔드포인트로 2다리 RFQ를 구현할 수 없다. 별도 `execute` 엔드포인트 역시 이 제한을 없애는 근거가 아니다. |

출시 공지 본문은 다중 다리를 홍보하지만 말미의 위험 고지에는 “한 번에 한 Call 또는 Put 계약만 지원”이라는 문구도 남아 있다. 두 서술의 적용 범위를 공개 문서만으로 해소하지 못했다. 이를 원자성의 긍정·부정 어느 쪽 증거로도 억지 해석하지 않는다. [FAQ](https://www.binance.com/en/support/faq/detail/6d3a80c6574f482eb45457eac64bbff6)는 일반 안내이고 법적 약정이 아니며 오래됐을 수 있다고 스스로 고지한다.

[개발자 문서의 공식 인터페이스 원칙](https://developers.binance.com/en/docs/introduction)에 따라 화면 내부 통신 추측·비공식 엔드포인트·GUI 스크래핑을 실거래 자동화의 대안으로 사용하지 않는다. 공식 API가 나중에 확인되더라도 **엔드포인트 존재**와 **두 다리 체결 계약**은 각각 따로 검증해야 한다.

## 현행 위험 규칙과 남은 조건

RFQ 자격은 지원 관할·옵션 계정·지식 설문·충분한 담보에 달려 있고, 매도 가능 자산과 최소 거래 수량도 별도 확인이 필요하다([FAQ](https://www.binance.com/en/support/faq/detail/6d3a80c6574f482eb45457eac64bbff6) §6~10). **이 사용자 계정의 실제 자격·수량·담보는 조회하지 않았다.** 공지는 빠른 변동 때 포지션 조정이나 종료가 불가능할 수 있다고 경고한다. 단일 주문이 설령 원자적이라도 [TRADING_CORE](../current/TRADING_CORE.md)의 거래당 1% 계좌 위험·10% 증거금·가용잔고 1.5배 및 거래소 측 보호/5초 비상 청산과의 충돌은 그대로 남는다. [75 보호 API 감사](75-btc-option-protection-api-audit.md)와 [76](76-btc-option-long-first-protection-study.md)의 `LIVE_BLOCKED`를 변경할 근거가 없다.

RFQ의 OTC 결합 견적은 [73의 공개 주문장 두 다리 호가](73-btc-option-two-leg-quote-overlap.md)와 동일 자료가 아니다. 공개 호가로 RFQ 실제 체결가·수수료 후 수익·체결 성공률을 계산하지 않는다. 전략 사전등록이나 수익 판정도 하지 않는다.

## 다음 결정의 경계

1. **RFQ 경로:** 공식 RFQ 다중 다리 API 문서와 부분 체결·실패 시 보증 조항을 거래소가 명시하기 전에는 자동화 연구의 실행 후보로 올리지 않는다. 공개 자료 범위의 추가 반복 검색은 같은 결론만 재확인할 가능성이 높다.
2. **순차 보호 롱 경로:** [76의 불변식](76-btc-option-long-first-protection-study.md)은 계속 연구용 필요조건이다. 옵션 보호 롱을 현행 선물형 거래소 보호주문과 다른 보호 수단으로 인정할지 **사용자의 별도 명시 결정**이 필요하다. 인정하더라도 한도 완화가 아니라 옵션 전용 청산·독립 담보·계좌 경로 위험을 설계·검증해야 한다.
3. 계정 읽기, 실계좌 주문, 옵션 Paper/데모 접속, 유료 자료 계약은 이번 진행 요청으로 승인받은 것으로 보지 않는다. 기존 T/v2·O/v1·O/v2·O/v3 Paper와 10/23·10/24 점검 일정은 그대로 유지한다.
