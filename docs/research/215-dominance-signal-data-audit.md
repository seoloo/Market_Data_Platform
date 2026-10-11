# 215 — USDT 도미넌스 하락 + BTC 도미넌스 상승 → 이후 24 시간 BTC 선물 롱 — 중복 · 자료 감사 → **`자료 부족` (손익 계산 안 함)**

작성: 2026-10-11 (Claude) · 트랙: 연구 Phase 0 (주문 없음 · 실거래 · Paper · 예약 변경 없음) · 상태: **`DATA_INSUFFICIENT` — 자료 감사에서 멈춤. 손익 · 신호 수익은 하나도 계산하지 않았다.**

## 0. 요청

- 사용자가 붙여 넣은 프롬프트 → **"이 프롬프트대로 작업 진행해줘"** (2026-10-11).
- 가설: 완성 4 시간봉에서 USDT.D 하락 · BTC.D 상승이 동시에 나오면 이후 24 시간 BTC 선물 롱의 비용 후 수익이 개선된다. 비교군 A(도미넌스 조건) · B(BTC 현물 4 시간 수익 > 0) · C(TOTAL 상승 · BTC.D 상승), 주 비교 A − C.
- 프롬프트의 중단 규칙: **"4 시간봉 자료를 확보하지 못하면 자료 부족을 보고 … 일봉 등으로 자동 변경하거나 가짜 자료로 결과를 만들지 마."** · "현재 공급량이나 현재 구성 종목으로 과거 도미넌스를 임의 복원하지 마" · "바이낸스 거래대금 비중을 시가총액 도미넌스로 취급하지 마" · "유료 구매는 하지 마".

## 1. 중복 감사

| 검색어 | 결과 |
|---|---|
| USDT.D · BTC.D · dominance · 도미넌스 · CRYPTOCAP | 연구 기록 없음. 60(엘리엇 EW-4) 의 '사용자 데모 차트에서 함께 보는 것' 목록에 '테더 도미넌스' 한 줄만 있음(시험 안 함) |
| TOTAL · 전체 시가총액 · 스테이블코인 비중 | 없음 |
| 시장 전체 상승 참여도 · 시장 공통 움직임으로 BTC 방향 | 직접 같은 것 없음. 가까운 것: H3(여러 코인 포지셔닝 롱숏) · R1(상대 강세) · TA(알트 추세) · 160 ETF 순유입(외부 흐름 → BTC 72 시간) — 모두 가격 · 흐름 기반이고 시가총액 비율이 아니다 |

**212 · 213 · 214 와의 차이**

| | 무엇을 쓰나 | 무엇을 예측 · 결정하나 |
|---|---|---|
| 212 | 코인 수익에서 BTC beta 를 뺀 잔차(코인 쪽) | 과매도 코인의 상대 회복(코인 롱 + BTC 숏) |
| 213 | BTC · ETH 자기 가격의 추세 강도 | 기존 추세 비중을 연속으로 |
| 214 | BTC · ETH 4 시간봉 자기 수익 | 이미 정해진 매수의 실행 시점 |
| **215 (이번)** | **시장 전체 시가총액 대비 USDT · BTC 비중의 변화** | 이후 24 시간 BTC 방향(새 진입 신호) |

→ 같은 가설을 같은 방식으로 검증한 적은 없다. 다음 번호 **215** 로 진행했다(208 은 다른 세션이 사용 중).

## 2. 자료 감사 — 필요한 것과 확보 가능성

| 필요 자료 | 출처 후보 | 확보 가능성 (무료 · 정당) |
|---|---|---|
| **USDT.D · BTC.D 4 시간봉 종가 (TradingView CRYPTOCAP 정의)** | TradingView | **불가.** 지수는 TradingView 가 직접 계산하고(공식 설명: "calculated by TradingView"), 공식 다운로드 API 가 없다. 차트 자료 내보내기 · 비공식 웹소켓 수집은 유료 기능이거나 이용 약관상 자동 수집 금지 |
| TOTAL · BTC · USDT 시가총액 같은 시각 4 시간 값 | CoinGecko | **불가.** 과거 전체 시가총액(`/global/market_cap_chart`)은 유료 플랜 전용. 무료(Demo) 코인별 시가총액은 **최근 365 일까지**이고 **90 일 넘으면 일봉(00:00 UTC)** 만 준다 → 2020 ~ 2026 4 시간 불가 |
| 〃 | Coinranking | 전체 과거 시가총액 · BTC 도미넌스는 유료(Professional), 무료는 1 년까지 |
| 〃 | CoinMarketCap | 과거 전체 지표 API 유료. 웹 내부 자료를 긁는 비공식 도구(R crypto2 등)는 약관상 회색이고 일 단위 스냅숏 |
| 〃 | Coin Metrics 커뮤니티 | BTC · USDT 개별 시가총액은 **일 단위**뿐, TOTAL(상위 125 전체) 없음 |
| BTC 현물 · USD-M 선물 1 분봉 · funding · mark · 주문 단위 | 바이낸스(이미 보유 — 210 캐시 · 212 거래소 정보) | 가능 — 그러나 도미넌스 없이는 A · C 를 만들 수 없다 |

**지수 정의 · 변경 (공식 문서로 확인한 것)**
- TradingView TOTAL = **Crypto Coins Screener 상위 125 개** 코인의 시가총액 합(코인별 = 마지막 가격 × 유통 공급량). 도미넌스 = 그 코인 / TOTAL × 100.
- 구성 범위가 바뀌어 왔다: 예전 설명은 '상위 50 개(DeFi 제외)' → '순위에 있는 모든 코인' → 지금 '상위 125 개'. 스테이블 제외 지수 등은 2025-05-19 이전 과거를 나중에 계산해 붙였다(공식 표기). → **지금 TradingView 차트의 과거 값은 당시 실시간으로 보이던 값과 다를 수 있다**(구성 · 공급량 수정 반영 여부 확인 불가).

**프롬프트가 금지한 대체 방법(쓰지 않음)**
- 일봉을 4 시간으로 보간 · 지금 공급량 · 지금 구성으로 과거 TOTAL 복원 · 바이낸스 거래대금 비중을 도미넌스로 사용 · 유료 구매.
- 최근 90 일 안의 CoinGecko 시간 단위 코인별 시가총액으로 상위 N 개를 합쳐 만드는 길도 있으나, ① TradingView 정의(상위 125 · 그 공급량 자료)와 다른 **별도 지표**이고 ② 약 540 개 4 시간봉 · 수십 번의 신호로는 아래 검정력 기준에 한참 못 미쳐, 원래 가설 검증이 아니므로 하지 않았다.

## 3. 검정력 (프롬프트 기준: A − C 연환산 산술 평균 +1%p)

- 프로젝트 규정(BACKTEST_AND_TESTING): 사전 등록 전 `n ≥ ((z + z_β) · SD / Δ)²` 로 계산, SD 는 측정값.
- **SD(A − C 일간 계좌 수익 차이)를 잴 자료가 없어 계산할 수 없다.** 구조상 단서만 적는다:
  Δln(USDT.D) = Δln(USDT 시가총액) − Δln(TOTAL). USDT 시가총액의 4 시간 변화는 발행 · 소각으로만 움직여 보통 매우 작고, TOTAL 의 4 시간 변화(가격 변동)는 그보다 훨씬 크다. 그러면 거의 모든 봉에서 sign(ΔUSDT.D) = −sign(ΔTOTAL) 이 되어 **A ≈ C** 다(둘이 다른 봉은 TOTAL 변화가 USDT 발행량 변화보다 작은 잔잔한 봉뿐). 이 경우 A − C 차이는 소수의 봉에서만 생겨 SD 가 작아도 평균 차이가 거의 0 이 될 가능성이 높다 — 자료가 있어도 '추가 가치 없음' 으로 끝날 공산이 큰 설계다(추정 · 검증 아님).

## 4. 결론

- **`자료 부족 (DATA_INSUFFICIENT)`** — TradingView CRYPTOCAP 과 같은 정의의 USDT.D · BTC.D(그리고 같은 시각 TOTAL · USDT · BTC 시가총액) **4 시간봉 과거 자료를 무료 · 정당하게 확보할 수 없다.** 프롬프트 규칙대로 일봉 · 대체 지표로 바꾸지 않고 멈춘다.
- **손익 · 신호 수익을 계산하지 않았다** → 기각도 PASS 도 아니다. 판정 받은 가설 수에 넣지 않는다(다중 검정 이력에는 '시도 · 자료 부족' 으로만 기록).
- **다시 열 조건:** ① 사용자가 직접 TradingView 에서 정당하게 내보낸 CRYPTOCAP:USDT.D · BTC.D · TOTAL 4 시간봉 파일(약관 범위 안)을 주거나, ② 유료 자료 구매를 사용자가 따로 승인할 때. 그때는 이 문서 §2 의 감사(시각 · 완성 봉 사용 가능 시점 · 수정 여부)부터 다시 하고, 결과 전에 계약을 커밋한다.
- 구조상(§3) A 와 C 가 거의 같을 가능성이 커서, 다시 열더라도 '추가 가치 없음' 이 가장 가능성 높은 결론이다.

## 출처

- TradingView, Where do I find crypto market capitalization and dominance? — https://www.tradingview.com/support/solutions/43000550480-where-do-i-find-crypto-market-capitalization-and-dominance/
- TradingView, new CRYPTOCAP symbols — https://www.tradingview.com/blog/en/new-cryptocap-symbols-52377
- CoinGecko API, coins/{id}/market_chart (Demo 365 일 · 90 일 넘으면 일봉) — https://docs.coingecko.com/v3.0.1/reference/coins-id-market-chart
- CoinGecko changelog 2022-10 (global market_cap_chart 유료) — https://docs.coingecko.com/changelog/25102022
- Coinranking, global market stats · historical data — https://coinranking.com/api/features/global-market-stats , https://coinranking.com/api/features/historical-crypto-data
- crypto2 (CoinMarketCap 비공식 수집 R 패키지) — https://cloud.r-project.org/web/packages/crypto2/refman/crypto2.html
