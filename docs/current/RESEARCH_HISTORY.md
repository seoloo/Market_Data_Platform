# 연구 이력 요약

기준일: 2026-09-24. ACCEPTED 전략 없음 (T는 사전등록 판정 PASS, 전향 관찰 중·실거래 미승인). 아래 기각은 각 사전등록 가설의 판정이며 자동매매나 모든 차트 분석의 불가능을 뜻하지 않는다.
선별 통계의 n은 독립 관찰 수이며 실제 체결 거래 수가 아니다. 미측정 PF/MDD/거래성과를 0으로 채우지 않는다.

## H1 — REJECTED

Hypothesis: 4h 추세·15m 되돌림·5m 모멘텀/거래량 재개가 비용을 넘는 우위.
TRAIN: 2021~2022(2020 warmup), 평가 209,762회, 신호 6,650개, 실제 체결 0건(비용/edge gate).
Net Expectancy: 승인 가능한 비용 후 우위 미확인. PF/MDD: 0거래 성과로 유효성 주장 불가.
VALIDATION/OOS: 이 판정으로 후속 검증 진행하지 않음. 프로젝트 내 일부 미래 연도 열람은 아래 노출 주의 적용.
Failure: 짧은 horizon 점수 정보력·비용 기준 미충족. 장기 horizon 검정력 문제와 구분.
Learned: 신호와 체결/수익을 분리하고 비용 대비 효과 크기를 먼저 확인해야 한다.
Do not retry: RSI/EMA 기간·점수선만 미세 변경해 동일 H1을 되살리기.
근거: [15 사후분석](../archive/research/15-h1-postmortem.md).

## H2 — REJECTED (효과 크기 불확실성 별도)

Hypothesis: 포지셔닝 쏠림 해소(OI/funding/롱숏비).
TRAIN: 2021+2023. 선택된 F5의 6h 평균 34.57bps, SE 9.96, n=421.
VALIDATION: 2024 F5만 검증. 6h 평균 15.08bps, SE 8.93, n=189, 2SE 하한 -2.79bps, 분위 단조성 실패.
위 평균은 조건부 신호 수익이며 검증된 순거래 기대값이 아니다. Net Expectancy/PF/MDD/실제 체결 수: 해당 선별의 거래 시뮬 지표로 미측정.
OOS: 최종 통과 없음.
Failure: 사전등록 재현 조건 미충족. 크기가 0인지 유의미한 양수인지 구분할 표본 부족 문제도 있음.
Learned: 공식 REJECTED를 지우지 않되 “효과가 정확히 0”이라고 단정하지 않는다.
Do not retry: 결과를 본 뒤 decile·창·신호 조합을 바꿔 통과시키기.
근거: [16 제안](../archive/research/16-h2-proposal.md), [17 TRAIN](../archive/research/17-h2-screen-result.md), [18 검증](../archive/research/18-h2-validation-result.md).

## H3 — REJECTED

Hypothesis: 시점별 다중 심볼의 상대 포지셔닝 신호 롱숏.
TRAIN: 2023~2024, 신호 5종 × horizon 3종. 최대 절대 평균 5.27bps, 최대 보수 하한 1.59bps < 22bps 비용.
Net Expectancy: 비용을 넘는 조건 미확인. PF/MDD/실제 체결 수: 선별 통계와 구분, 미측정.
VALIDATION 2025 / TEST 2026: TRAIN 탈락으로 미실행.
Failure: 비용 기준 미충족. 원자료 digest·15개 조합 완전성 재판정도 기각.
Learned: funding 누락을 기각으로 읽지 않도록 선별 전 자료 적재율 검사. 폐지 종목 포함 causal universe 필요.
Do not retry: 사전등록 비용/표본 기준 낮추기, 누락 신호를 실패로 처리, 결과 뒤 신호 조합/방향 변경.
근거: [22 전환](../archive/research/22-multi-symbol-pivot.md), [23 사전등록](../archive/research/23-h3-preregistration.md), [24 결과/감사](../archive/research/24-h3-screen-result.md).

## R1 — REJECTED

Hypothesis: 21일 상대 추세 롱3/숏3가 비용 이후에도 지속.
TRAIN: 2023~2024. 1일 순평균 -19.42bps(n=699), 3일 +4.31bps(n=227), 3일 SE 29.88·2SE 하한 -55.46bps.
순평균=가격+funding-22bps. 3일은 2023 양수/2024 음수, horizon·분위 구조 불일치.
PF/MDD/실제 체결 수: 선별 결과로 미측정. n을 거래 수로 보고하지 않는다.
VALIDATION 2025 / TEST 2026: 미실행.
Learned: 양의 표본평균만으로 비용 후 우위를 주장할 수 없다.
Do not retry: 7/14/28일 lookback·leg 크기 탐색 또는 방향 반전으로 같은 연구를 살리기.
근거: [25 방향 검토](../archive/research/25-research-direction-review.md), [26 사전등록](../archive/research/26-r1-preregistration.md), [27 결과](../archive/research/27-r1-screen-result.md).

## A1 — DATA_INSUFFICIENT (사용자 결정으로 종료, 2026-09-23)

Hypothesis: 변동성 수축(24h σ가 30일 하위 30%) 뒤 200h 추세 방향 6h 레인지 돌파는 2h 보유로 비용 전 42.44bps 이상 번다.
TRAIN: 2021-01-03~2022-12-31, 대조군(수축 조건 제거) 동시 실행, 같은 `decide()`·Risk·G6 달력.
Result: 주 arm 실제 거래 **102건 < 사전등록 최소 146건 → `DATA_INSUFFICIENT`**. 대조군 45건.
T1~T5 손익 통계는 규칙대로 계산·열람하지 않았다. PF/MDD/Net expectancy: 미측정.
Failure: 두 arm 모두 계좌 누적 DD 10% 정지(`MAX_DRAWDOWN`, 자동 해제 없음 — 2026-09-24 사용자 정정으로 30%가 됨, A1은 재실행하지 않음)에 걸려 표본이 잘렸다
(거부 주 arm 125·대조군 1,014). 이 집계가 성과 방향(10% 이상 하락)을 간접 노출했다.
`VOL_TOO_LOW`(1h ATR 연 하위 5%)도 수축 국면 진입 153건을 막았다.
Learned: 예상 표본은 빈도 한도뿐 아니라 **계좌 수준 정지(DD·일 손실·연패 쿨다운)와 G5 게이트의 상호작용**까지
포함해 추정해야 한다. 전략 우위 측정과 계좌 위험 규칙 적용을 설계 단계에서 분리해 등록한다.
Do not retry: 같은 TRAIN으로 계좌 정지만 끄고 A1을 재판정하기(노출 후 규칙 변경), 파라미터 8개 조정, A2 생성.
근거: [02 사전등록 §11·§12](../research/02-a1-preregistration.md), [03 감사](../research/03-a1-pretrain-audit.md),
[05 G6 원천](../research/05-a1-macro-pit-source-audit.md).

## WA — REJECTED (2026-09-24)

Hypothesis: 24h 하락 뒤 1h 종가가 7일 고저 범위 하위 20%인 상태에서 6h 보유(2σ·√6 손절)는 한 방향으로 비용 전 60bps 이상 번다. 방향은 TRAIN 부호로 정한다.
TRAIN: 2023~2024, 우위 측정 모드(계좌 정지·G6 비적용, BACKTEST 전용), 주 arm·대조군(상태 조건 제거) 롱·숏 각각 재생.
Result: 거래 롱 210·숏 240(관문 176 통과). 롱 비용 전 −4.05bps → `D = short`.
숏 순평균 **−23.23bps**(2SE 하한 −39.74), 비용 전 −4.18bps(< 60), 대조군 대비 −1.78bps(하한 −19.58),
2023 −31.43·2024 −15.16. T1~T4 전부 실패, 강제청산 0. 전체 규칙 계좌 재생 NOT_RUN(G6 달력 없음). PF/MDD: 미측정.
VALIDATION 2025~2026: 미실행.
Failure: 상태 조건에 6h 방향 정보가 없다(롱·숏 비용 전 모두 ±4bps). 실제 비용 약 20bps가 그대로 손실이 됐다.
Learned: 재량 트레이더의 진입 **맥락**(하락 뒤 진입)을 기계적 상태로 옮기면 우위가 사라질 수 있다. 관찰된 맥락은 필요조건 후보일 뿐, 수익원 증거가 아니다.
Do not retry: 창(24h·7일)·경계(20%)·보유(6h/12h)·손절 배수 조정, 방향 반전, WA2 생성, 같은 TRAIN으로 G6 적용 재판정.
근거: [06 탐색](../research/06-wonyotti-exploration.md), [07 후보 비교](../research/07-w-candidate-comparison.md), [08 사전등록 §10](../research/08-wa-preregistration.md).

## K — REJECTED (VALIDATION, 2026-09-24)

Hypothesis: 현물 BTC 롱 + USD-M 숏(같은 수량, 선물 2배) 델타 중립으로 funding을 받으면 비용 후 연 5% 이상. 7일 평균 funding ≥0.005% 진입·<0 해제.
TRAIN 2023~2024: 연환산 **6.28%**(2023 4.76·2024 7.83), 월평균 하한 +0.32%, MDD 1.40%, 청산 0 → PASS.
VALIDATION 2025~2026-08: 연환산 **1.97%** < 5% → **REJECT**(K2·K3·K4·안전은 통과). STRESS 2020~2022: 11.11%, MDD 2.34%, 청산 0.
Net: funding이 수익의 거의 전부, 비용은 진입·해제·재조정 합계 funding의 8~31%, 베이시스 영향 무시 가능. PF: 해당 없음(연속 보유).
Failure: funding 국면 축소. 과열기(2021) 연 23% → 2023~2024 연 5~8% → 2025~2026 연 2~3%. 최근은 현금성 자산보다 낮다.
Safety: 상시 보유 대조군이 2021-07-26 선물 1h +31% 급등에 2배 숏 청산 → 2배는 불충분, 실거래라면 1배 또는 포트폴리오 마진.
Learned: 방향 예측이 아닌 구조적 수익원은 실제로 존재했고 비용 후에도 양수였다. 다만 크기가 국면에 따라 한 자릿수 %로 줄어든다.
Do not retry: 진입·해제 임계·신호 창·레버리지를 바꿔 같은 VALIDATION 재판정, 결과를 본 뒤 기간 자르기.
근거: [09 후보 비교](../research/09-next-candidate-comparison.md), [10 사전등록·§10 결과](../research/10-k-carry-preregistration.md).

## M — REJECTED (TRAIN, 2026-09-24)

Hypothesis: 월별 유동성 상위 20(현물 페어 필수, 폐지 포함 목록) 중 7일 funding 상위 최대 4종목을 선물 1x 델타 중립으로 보유하면 비용 후 연 5% 이상.
TRAIN 2023~2024: 연환산 **3.65%** < 5% → **REJECT**. 월평균 하한 +0.09%, 2023 1.96%·2024 5.37%, MDD 1.25%, 청산·강제 정리 0.
Net: funding 1,225 − 비용 505(41%) + 베이시스 24. 거래대금 순 대조군 3.08%. 진입 72·재조정 63, 보유 종목 55.
VALIDATION/STRESS: 미실행.
Failure: 알트 캐리(현물 명목 대비 연 약 12%)가 BTC(약 10.6%)보다 크게 높지 않았고, 교체·재조정 비용과 1x의 자본 효율(50%)이 수익을 절반 이하로 줄였다.
Learned: 캐리 계열은 실재하지만(K·M 모두 모든 연도 양수, 월간 하한 양수) 안전한 레버리지에서 자본 대비 연 2~5% 수준이다. 종목 선택은 크기를 거의 키우지 못했다.
Do not retry: 슬롯 수·문턱·유니버스 크기·레버리지·재조정 폭 조정으로 같은 TRAIN 재판정.
근거: [11 후보 비교](../research/11-post-k-candidate-comparison.md), [13 사전등록·§10 결과](../research/13-m-multi-carry-preregistration.md).

## T — PASS (사전등록 1회 판정, 2026-09-24) · 전향 관찰 중 · 실거래 미승인

Hypothesis: BTC·ETH 절대 추세(20·60·120일 부호 평균, 변동성 목표 15%, 롱 현물·숏 선물 1x)는 비용·funding 후 일간 순수익 평균 > 0.
판정 2018-01~2026-08 (8.67년, 독립 검증 구간 없음 — 14 §3): 일간 평균 0.060%, 2SE 하한 +0.017% → T1 통과. 두 절반 모두 양수 → T2 통과. 청산 0.
연 샤프 **0.95**, CAGR 21.2%, MDD 25.4%. 2018~2021 샤프 1.50 → 2022~2026 **0.46**. 롱 +51,057 · 숏 −1,527(+funding 1,290) · 비용 7,899.
보고: 롱 전용 샤프 1.20·MDD 15.9%, BTC·ETH 보유 샤프 0.64·MDD 87.9%.
Caveat: 관측 샤프가 검정력 기준(0.96)과 같은 크기, 뒤 절반 약화, 수익은 롱 쪽, 큰 추세 구간은 공지의 사실. (DD 10% 불일치 지적은 규칙 오기록이었음 — 계좌 정지 30%로 정정, MDD 25.4%는 그 안.)
Learned: 기간을 늘린 설계로 처음 판정 가능해졌다. 추세 추종의 실질 가치는 초과 수익보다 **낙폭 축소**에 가깝다.
Do not retry: lookback·변동성 목표·밴드·자산 조정으로 같은 기간 재판정. 롱 전용 전환은 새 사전등록 + 전향 확인으로만.
근거: [14 검정력 검토](../research/14-trend-power-review.md), [15 사전등록·§7 결과](../research/15-t-trend-preregistration.md).

## V — REJECTED (2026-09-24, V/v1 무효 → V/v1.1 판정)

Hypothesis: BTC·ETH 당일 시가 ± 0.5×전날 폭 돌파 방향으로 진입해 당일 종가 청산하면 비용(24bps) 후 거래당 순손익 > 0.
V/v1: 표면 PASS(+56.5bps)였으나 **무효** — "양방향 동시 돌파일 제외"가 그날 전체 고저를 보는 미래 정보로, 손실 거래를 체계적으로 뺐다(사전등록 설계 오류).
V/v1.1 (1시간봉으로 먼저 닿은 쪽 진입, 판별 불가 시 나쁜 쪽): 거래 4,722건, 순손익 **−0.5bps**(하한 −10.9), 2017~2021 +10.2 · 2022~2026 −10.4 → **REJECT**.
비용 전 +23.5bps ≈ 비용. 롱 +20.6 / 숏 −20.8.
Learned: (1) 일봉 OHLC로 당일 체결을 흉내 낼 때 **같은 날의 고저로 거래를 거르면 미래 정보**가 된다 — 사전등록 검토 항목으로 추가.
(2) 당일 돌파 모멘텀도 비용을 넘지 못한다(H1·A1·WA와 같은 결론).
Do not retry: k·청산 시점·필터 추가로 같은 기간 재판정.
근거: [17 후보 비교](../research/17-futures-long-short-candidates.md), [18 사전등록·§5~§7](../research/18-v-breakout-preregistration.md).

## 비용·프로젝트 결정 이력

[19 maker 비용 추정](../archive/research/19-maker-cost-measurement.md)의 초기 낙관적 결과는 [20 신호 시점 체결](../archive/research/20-signal-time-fills.md)의 역선택 측정으로 대체됐다. 비용 절감 경로는 당시 REJECTED.
[21 종료 기록](../archive/research/21-closure.md)은 이후 철회된 역사이며 현행 프로젝트 종료 명령이 아니다.
D22는 미완성/제외된 초안으로 연구 판정(ACCEPTED/REJECTED/INCONCLUSIVE)을 받은 전략이 아니다.
H1/H2를 전부 “단일 심볼 표본 부족 때문”으로 뭉뚱그리지 않는다. 관측 효과 부족과 검정력 부족을 구별한다.
2024~2026 일부는 과거 개발·비교에서 노출됐다. 새 연구의 OOS를 보장하려면 노출 기록과 동결 후 전향적 구간이 필요하다.

## 다음 연구 기록 양식

ID/version · Hypothesis · ACCEPTED/REJECTED/INCONCLUSIVE · TRAIN/VALIDATION/OOS 각각의 결과 · Net expectancy/PF/MDD/실제 trade count · 선별 n 별도 · Failure reason · Learned · Do not retry · 사전등록/코드/config/data/result 참조.
자료 누락/검정 중단을 전략 REJECTED로 변환하지 않는다. 관찰에서 얻은 개선 아이디어는 별도 가설로 등록한다.
후보 비교와 승인 절차는 [BACKTEST_AND_TESTING](BACKTEST_AND_TESTING.md)을 따른다.
