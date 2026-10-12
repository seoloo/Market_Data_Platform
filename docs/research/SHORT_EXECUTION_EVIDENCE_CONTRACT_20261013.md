# 고정 숏 — 호가·체결 증거·계좌 일치성 검증 계약

작성: 2026-10-13 Codex. 운영 Phase6 유지, 신규 연구 Phase0 / UNVERIFIED.
상태: **DESIGN_FROZEN / NO_NEW_PERFORMANCE**. 아래 §1~7은 구현 전 설계, §8은 완료 기록용.

## 1. 범위와 질문

사용자 “좋아 다음 작업해줘”는 직전 제안인 실제 호가·부분체결·계좌 일치성 검증 설계의 후속이다.
기존 RSI7/70+완성일봉ADX 하락, +Keltner 두 규칙의 숫자/판정은 변경하지 않는다.
새 조합/PnL 재계산/Laya 질문·문턱 변경/계좌 조회/실제 주문/새Paper·Shadow·예약은 없다.
연구208/그 자료, 2026-09-01 이후 보존 시장자료, 환경변수와 기존 실행기를 읽거나 수정하는
범위로 확대하지 않는다. 기존 실행기 소스·저장 스키마는 정합성 설계에 필요한 읽기만 한다.

질문은 수익 우위가 아니라 **앞선 후향 체결 근사의 무엇이 실제 실행에서 아직 증명되지 않았는가**다.
익명 합성 체결로 회계를 검산한다. 키 없는 현재 공개 BTC/ETH 호가의 단발 구조 시험을 최대6GET로
허용한다(서버시각1/필터1/각종목 mark·depth 각1). 반복 수집·자동 재시도·우회·유료 구매는 없다.
이 현재 호가는 역사적 체결이나 새 수익OOS로 사용하지 않는다. 접속 실패도 그대로 기록한다.

## 2. 확인한 자료·구현 차이

- `data/external/binance_vision/`에서 book/depth/quote 이름 파일 검색0. 전체 프로젝트의
  호가 부재 증명은 아니며, 승인되지 않은 다른 데이터나 현재 계좌 원장은 열지 않는다.
- `BinanceUsdmPublic`은 일봉/funding/현재가만 제공하고 depth 이력·체결 증거는 제공하지 않는다.
- 기존 `FuturesOrderManager.submit_capped`는 **매번 mark를 재조회하여 ±0.3%** 한도를 정한다.
  직전봉 한도를 쓰는 후향 모형과 다르다. 재설정 한도는 최초 mark 대비 누적 가격 한도 보장이 아니다.
- 기존 Fake는 mark에 체결하고 `fill_ratio`로 부분량을 정한다. 실제 호가/queue 모형이 아니다.
- 주문 조회의 fee0은 수수료 미포함일 수 있다. 실제 체결 목록과 commission 자산·수량 대조가 필요하다.
- `fills` 스키마에는 trade_id·ts_ms·수량·가격·fee·asset이 있지만 수신시각·공급자·
  시장/전략 경계·출처digest가 충분하지 않다. 기존 DB migration을 바꾸지 않고 별도 연구 계약을 둔다.

## 3. 호가 계약 — 체결 확인과 구분

새 순수 모듈은 HTTP/DB/시계/주문 import 없이 명시 입력만 받는다. 모든 금액/수량은 Decimal.
BTCUSDT/ETHUSDT, nonempty bids/asks, 가격/수량 양수 finite, 정렬/중복/교차 여부를 검사한다.
시세는 received_ms≤as_of_ms여야 하고, 수신·거래소 시계 offset/불확실성을 별도 보존한다.
서버시각 한 요청의 midpoint offset과 반RTT 불확실성을 기록하되 정밀 시계 동기화로 인증하지 않는다.

고정 품질 기준: 수신 age≤1000ms, clock uncertainty≤250ms,
보정 거래소→수신 지연은 uncertainty 포함 최대1000ms, mark/depth 수신차≤1000ms,
spread≤10bps. 미래·stale·시계 불명·순서/단위 오류는 fail-closed.
tick/step/minQty/minNotional은 PRICE_FILTER/LOT_SIZE/MIN_NOTIONAL을 사용한다.
pricePrecision/quantityPrecision을 호가/수량 단위로 대신하지 않는다.

가격한도는 mark±30bps, BUY 내림/SELL 올림, 수량은 step 내림.
같은 호가에서 보이는 수량100%와25%를 각각 **가시 호가의 가정된 사용량**으로 계산한다.
두 비율은 고정 자료 민감도이며 실제 체결률·실체결 하한/상한의 인증이 아니다.
원하는 수량보다 호가가 적으면 잔여량을 남긴다. limit밖 가격은 사용하지 않는다.
결과는 `INDICATIVE_ONLY`, filled 대신 indicative_qty/VWAP/remaining을 쓴다.
IOC·post-only/시장가의 실제 체결 비교나 보호/강제청산 검증으로 확대하지 않는다.

## 4. 체결 증거 계약 — 주문 상태와 회계

별도 불변 OrderEvidence/FillEvidence: venue·strategy·symbol·order_id·trade_id·방향,
요청/누적수량·지정가·decision_ms·exchange_ms·received_ms, 실제 fee/asset, terminal/UNKNOWN.
주문 의도/체결에 연구 경계를 넣고 실제 원장의 개인 ID를 보고서나Git에 넣지 않는다.
다른 시장·종목·전략의 같은 trade_id는 별개이며, 같은 경계/ID의 동일 재전송은1회만 집계한다.
같은 ID의 서로 다른 경제적 값은 충돌 오류다. 동일 체결의 늦은 수신 중복은 경제값만 비교한다.
as_of 이후 수신 사건은 사용하지 않는다. 주문/체결의 미래 발생/체결 전 결정/비정상 수량도 거부한다.

확정 주문 누적량과 고유 체결 합이 **정확히 같아야** 체결 장부가 일치한다.
부분량이 있으면서 EXPIRED/CANCELED인 주문도 포지션은 남는다. terminal=포지션0으로 해석하지 않는다.
UNKNOWN/PENDING/증거 부족은 INCOMPLETE, fee=None/비USDT는 미확인으로 거부한다.
수수료0을None 대신 채우지 않는다. 동일 의도의 전체 체결은 원래 요청량을 넘지 않아야 한다.

합성 SHORT ledger는 SELL의 가중평균 진입/BUY의 부분축소 gross/실제fee와 signed funding cash/
마지막 평가가격의UPnL을 분리한다. 평균 진입가를 청산 때 바꾸지 않는다.
funding은 이미 확인된 signed USDT cash로 입력하며 mark×rate에서 새로 발명하지 않는다.
동일 ms의 증가는 축소와 순서가 모호하면 거부한다. 다른 ms는 거래소 시각 순, 입력 순서와 무관.
과잉BUY/역방향 전환은 거부하고, equity=초기cash+realized−fee+funding+UPnL 잔차0을 확인한다.
이 ledger는 위험·보호·강제청산 엔진이 아니며 기존 G5/G6나 Phase5/6 통과를 인증하지 않는다.

## 5. 합성 검증·기존 코드 대조 계획

- 고정 호가에서 BUY/SELL·가격한도·tick/step·partial·empty/depth不足·stale·future·clock·spread.
- 누출: 미래 호가/체결을 추가해도 과거 판단·장부 불변, 수신 전 자료 사용 불가.
- 체결 중복/다른시장ID/충돌/fee결측·자산/초과체결/terminal부분·UNKNOWN/순서모호 반례.
- SHORT 부분진입/부분청산/funding/UPnL과 손계산 기대값, 순서 뒤집기·중복 재생 불변.
- 테스트 안에서만 기존 Fake+manager+인메모리DB로 동일 synthetic 수량/fee를 대조한다.
  초기mark 50000→각재시도1%하락·부분체결 반례로 최초한도 위반 가능성을 **발견/기록**한다.
  운영 코드 수정·새 한도 적용은 하지 않는다. Fake 일치는 실제 거래소 일치가 아니다.
- 프로젝트 전체pytest/leakage/mypy/ruff/import계약 검사를 수행하고 기존 위반은 분리 보고한다.

## 6. 다음 단계의 준비 여부

완료 기준은 설계+순수감사구현+합성/누출/기존Fake대조+한정 공개자료 구조시험/실패기록이다.
실제 개인 체결·보호 first-fill/정산/계좌 스냅숏이 없으므로 `REAL_EXECUTION_NOT_VALIDATED` 유지.
후향 수익·최적비율·새PASS를 계산하지 않는다. 이전 검정력 계획·기존 판정도 그대로다.

후속 장기관찰 제안: 실제 수신 기준 event를 보존, closed4h 의도/호가/실측 fee·부분체결·
잔량·보호시각·UNKNOWN을 연결. quote-only 모형과 실제체결 증거 장부를 서로 다른 상태로 둔다.
실계좌 조회/테스트넷/새Paper·Shadow·예약 활성화는 이번 다음작업 지시로 자동 실행하지 않는다.
quote-only 12개월도 수익우위/실제fill률/80%검정력 통과가 아니다.

## 7. 확인한 원문

[Binance USD-M 시장자료](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data): depth·서버시각·exchangeInfo·mark 계약.
[Binance USD-M 체결자료](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade): 실제 체결 목록은 USER_DATA이며 이번에는 호출하지 않는다.
[이전 고정 비교](SHORT_EXECUTION_LAYA_VALIDATION_20261013.md)§11, 미사용OOS0/전향검정력 부족 유지.

## 8. 완료 기록

구현·합성 결과·공개 단발 시험/실패·검사 수·자료/코드hash는 완료 후 이 절에만 추가한다.
