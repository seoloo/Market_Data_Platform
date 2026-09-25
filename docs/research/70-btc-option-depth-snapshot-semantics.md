# 70 — BTC 옵션 무료 depth 프레임의 스냅샷 의미 확인

작성: 2026-09-25 · Phase 0. [69 무료 L2·단독 다리 위험 게이트](69-btc-option-free-depth-and-leg-risk.md)의 **자료 형식** 후속이다. 유료 자료 구매·패키지 설치·원시 파일 저장·계정 인증·주문·손익 계산·운영 변경은 없었다.

## 결론과 범위

`TOP_N_SNAPSHOT_SEMANTICS_CONFIRMED / EXECUTION_QUALITY_NOT_PROVEN / ACCOUNT_RISK_NOT_CLEARED`.

[Tardis 공식 옵션 매퍼 원본(검사 커밋 `3361bcd310c6de608a1fda16c1976cb814c07dde`)](https://github.com/tardis-dev/tardis-node/blob/3361bcd310c6de608a1fda16c1976cb814c07dde/src/mappers/binanceeuropeanoptions.ts)은 2025-12-17 전 `depth100`, 이후 `depth20`을 선택한다. **두 매퍼 모두 각 원시 depth 프레임의 `b`·`a` 전체를 `book_change(isSnapshot: true)`로 변환한다.** 구형 `pu=u`가 누락의 증거가 아니고, 신형 `pu=직전 u`가 첫 주문장을 만들기 위한 유일한 방법도 아니다. [Binance 옵션 partial-depth 명세](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/ws-streams/public)는 신형 `depth{level}`이 최상위 5·10·20 bid/ask 레벨을 보낸다고 설명한다. [Tardis 옵션 피드 명세](https://docs.tardis.dev/historical-data-details/binance-european-options)는 구형 `depth100`과 신형 `depth20`의 채널 전환 및 상위 100/20 레벨을 명시한다.

따라서 [69](69-btc-option-free-depth-and-leg-risk.md)의 `SNAPSHOT_NOT_PROVEN`은 **원시 프레임에 명시적 플래그가 없어서 초기 스냅샷 생성 자체가 불가능하다**는 뜻으로 유지할 수 없다. 공급처 매퍼의 형식 의미상 첫 수신 프레임과 재연결 뒤 첫 프레임을 각각 독립적인 **상위 N레벨 스냅샷**으로 시작할 수 있다. 하지만 이는 실제 거래소 전체 주문장·캡처 누락 없음·표시 수량의 체결 가능성에 대한 독립적 보증은 아니다. 공식 Node 클라이언트를 설치·실행한 결과가 아니라 **매퍼 소스 감사 + 제한된 무료 원시 표본 대조**라는 경계를 유지한다.

## 무료 표본 대조

[Tardis HTTP API](https://docs.tardis.dev/api/http-api-reference)의 API 키 불필요한 월 1일 공개 NDJSON을 읽기 전용으로 재조회했다. 반환값은 메모리에서만 파싱했고, 공급처 `localTimestamp`와 메시지 순서를 사용했다. 사용자 계정이나 비공개 엔드포인트는 이용하지 않았다.

| 고정 요청 | 관찰 | 스냅샷 해석의 한계 |
|---|---|---|
| `2026-09-01`, `depth20`, `btc-260904-71000-p`, 00:00–00:01 UTC | 21프레임. 첫 00:00:01.1098976에 bid 3·ask 11레벨, 최우선 `25.000/26.59`·`30.000/15.40`; 다음 00:00:05.3505163에도 bid 3·ask 11레벨, ask 최우선 수량 `35.40`. `pu`는 첫 `35613336383`, 다음은 이전 `u=35613346632`. | 각 프레임은 상위 20개 *이하*의 양쪽 가격·수량 목록. 첫 관측 이전 상태와 프레임 사이 주문·취소·체결 우선순위는 알 수 없음. |
| `2025-12-01`, `depth100`, `BTC-251205-90000-P`, 00:00–00:10 UTC | 공급처 연결 중단 빈 줄 1개. 직전 00:09:02.1058575에는 bid 2·ask 3레벨, 최우선 `2310/11.75`·`2395/0.24`; 직후 00:09:02.9467850에는 bid 1·ask 3레벨, 최우선 `5/10`·`2370/0.24`. 두 프레임 모두 `pu=u`. | 재연결 후 첫 프레임부터 상위 100레벨 스냅샷으로 **새로 시작**한다. 이전 프레임의 레벨을 이어 붙이면 안 된다. 재관측 전 약 0.84초는 `UNKNOWN`; 직후의 극단적 스프레드는 실행 품질 거부 사유다. |

재현 요청은 각각 `GET /v1/data-feeds/binance-european-options`의 `from=2026-09-01`, `offset=0`, `sliceSize=1`, `filters=[{"channel":"depth20","symbols":["btc-260904-71000-p"]}]` 및 `from=2025-12-01`, `offset=0`, `sliceSize=10`, `filters=[{"channel":"depth100","symbols":["BTC-251205-90000-P"]}]`이다. 심볼 대소문자는 공급처 채널의 당시 형식에 맞췄다. 이 조회는 전체 날짜·전체 체인 검사가 아니다.

## 다음 자료 게이트에 반영할 규칙

1. `depth100`/`depth20` 프레임은 **누적 delta로 적용하지 말고**, 계약별 기존 레벨을 버리고 해당 프레임의 상위 N레벨로 교체한다. 빈 bid/ask는 이전 레벨 보존이 아니라 그 시각 표시 측면의 결손이다. `depth20`을 전체 주문장으로 승격하지 않는다.
2. 공급처 빈 줄(연결 중단)·메시지 공백·시퀀스 이상 구간은 마지막 정상 호가를 무기한 운반하지 않는다. 재관측 전은 `UNKNOWN`; 재관측 첫 프레임이 와도 별도의 양방향 수량·스프레드·시각 신선도 검사를 통과해야 한다. 신형 `pu` 검사는 보조 무결성 지표로 유지하되, 전체 스냅샷 의미와 혼동하지 않는다.
3. 공식 매퍼의 `isSnapshot=true`는 **자료 형식의 해석**이다. Tardis 캡처의 완전성, 호가 깊이의 시장 대표성, 거래소 호가와의 독립 교차검증, 실제 주문의 체결·슬리피지, 옵션/선물 계좌 위험은 여전히 미검증이다. 특히 [69의 보호 다리·증거금·거래소 측 보호 충돌](69-btc-option-free-depth-and-leg-risk.md)은 그대로 실거래 차단 사유다.

이번 정정으로 **무료 자료에서 제한된 상위 N레벨 스냅샷을 읽을 수 있다**는 것까지만 전진한다. 수익성 판정·사전등록·전략 채택을 시작하지 않는다. 다음에는 동일한 사전 고정 계약/시간 표본에서 스냅샷별 유효 양방향 호가·스프레드·표시 수량·관측 간격을 계약 유형별로 집계하고, 무료 월 1일 표본의 일반화 한계를 먼저 판정해야 한다. 계정 인증이나 옵션 매도 실행 설계는 별도 승인 범위다.
