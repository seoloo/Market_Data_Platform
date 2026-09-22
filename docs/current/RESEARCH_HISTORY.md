# 연구 이력 요약

기준일: 2026-09-23. ACCEPTED 전략 없음. 아래 기각은 각 사전등록 가설의 판정이며 자동매매나 모든 차트 분석의 불가능을 뜻하지 않는다.
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
