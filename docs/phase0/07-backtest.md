# 07 — 백테스트 엔진 및 검증 설계 (I)

## 1. 기본 방침

| 항목 | 선택 | 이유 |
|---|---|---|
| 방식 | **이벤트 기반 봉 재생** (벡터화 아님) | 상태(포지션·쿨다운·리스크 한도)가 경로 의존적. 벡터화하면 라이브와 로직이 갈라진다 |
| 코드 공유 | `decide()` 를 라이브와 **동일 함수**로 호출 | 일치성의 유일한 보장 수단 |
| 시간 전진 | 1분 단위 | 최소 해상도에 맞춤. 상위 TF 는 마감 시점에만 갱신 |
| 체결 해소 | 항상 **1분 데이터로 봉 내부 경로 해소** | 상위 TF 전략도 1m로 SL/TP 순서 판정 |
| 외부 라이브러리 | 사용 안 함 (backtrader/vectorbt 등) | 라이브와 코드 경로를 공유할 수 없음 |

---

## 2. 타이밍 규칙 (look-ahead 차단의 핵심)

```
봉 [T_open, T_close]  마감
   │
   ├─ as_of = T_close        ← 이 시각 "이하"의 마감봉만 스냅샷에 포함
   │
   ├─ decide() 실행 → Intent 생성
   │
   └─ 체결은 다음 봉 [T_close+1ms, T_close+60s] 에서 발생
```

| 규칙 | 내용 |
|---|---|
| R-1 | 신호 봉의 종가로 체결하지 않는다. **다음 봉 시가 ± 슬리피지** 가 기본 체결가 |
| R-2 | 상위 TF 봉은 그 봉이 **마감된 후** 에만 스냅샷에 포함 (4h 봉은 04:00에 마감 → 04:00 이후 사용) |
| R-3 | 지표 warm-up 미충족 구간은 거래하지 않는다 |
| R-4 | 정규화는 causal 창만 사용 (전체 표본 통계 금지) |
| R-5 | ZigZag/스윙은 `confirmed_at <= as_of` 인 것만 노출 (→ 06 §7) |
| R-6 | 거시 데이터는 `vintage_ms <= as_of_ms` 인 행만 조회 (→ 03 §3.6) |
| R-7 | 뉴스는 `published_ms + ingest_delay <= as_of_ms` 인 것만 (기본 delay 60s) |

### 봉 내부 경로 해소 (intrabar resolution)

SL 과 TP 가 같은 봉 범위 안에 모두 들어오면 어느 쪽이 먼저인지 봉 데이터만으로는 알 수 없다.

```
if 1m 데이터 사용 가능:
    1m 봉을 순서대로 재생하여 실제 도달 순서 판정   ← 기본
else:
    비관적 가정: SL 이 먼저 체결된 것으로 처리
```

1m 봉 내부에서도 애매하면 **비관적 가정**을 쓴다. 백테스트는 낙관적일수록 쓸모없다.

#### ⚠ 1분봉의 거래량 0 채움봉 주의

실데이터 확인 (2026-09-21): Binance 1m 아카이브는 **거래가 없는 분을 직전가로 채운다.**

```
2024-10-28 21:10  O=H=L=C=69566.10  vol=0   ← 채움봉 (실제 체결 없음)
2024-10-28 21:11  O=H=L=C=69566.10  vol=0
2024-10-28 21:14  O=69650.00        vol=32.637  ← 실제 체결 재개
```

봉 내부 경로 해소에 이 봉을 그대로 쓰면 **체결되지 않았을 스톱이 체결된 것으로 처리**될 수 있다.
채움봉의 가격은 직전 체결가이지 그 시점의 시장가가 아니기 때문이다.
위 사례에서 21:10 의 69566.10 과 21:14 의 69650.00 사이에는 실제 거래가 없었다.

→ `FillSimulator` 는 `volume == 0` 인 1m 봉에서 **체결을 발생시키지 않는다.**
   유동성이 없던 구간이므로 그 가격에 체결될 수 없다.
→ 이 규칙은 단위 테스트로 고정한다.

---

## 3. 체결 시뮬레이션

### 3.1 주문 유형별

| 주문 | 체결 규칙 |
|---|---|
| MARKET | 다음 1m 봉 시가 + 슬리피지(불리한 방향) |
| LIMIT | 봉 범위가 지정가를 **통과**해야 체결. 단순 터치는 부분 체결 확률로 처리(기본: 터치만으로는 미체결) |
| STOP_MARKET | mark price 가 stop 을 통과한 1m 봉에서 발동, 체결가 = stop ± 스톱 슬리피지 |
| TAKE_PROFIT_MARKET | 동일 |
| reduce-only | 포지션 수량 초과분은 자동 축소 |

**LIMIT 을 낙관적으로 처리하지 않는 것**이 중요하다. 지정가에 닿았다고 반드시 체결되지 않는다.
MVP 전략은 시장가 진입만 사용해 이 불확실성을 피하는 것을 권장한다.

### 3.2 슬리피지 모델

```python
slippage_bps = (
    base_bps                                    # 기본 1.0
  + vol_coef * atr_percentile                   # 변동성 비례
  + size_coef * (notional / rolling_quote_vol)  # 시장 충격 근사
)
slippage_bps = min(slippage_bps, cap_bps)       # 기본 상한 30
# 스톱 주문은 추가 페널티 (연쇄 청산 구간에서 체결되므로)
if is_stop: slippage_bps *= stop_multiplier     # 기본 2.0
```

계수 초기값은 **보수적 추정**이며, Phase 8 소액 실거래의 `trades.slippage_*_bps` 실측치로 보정한다.
보정 전후 백테스트 결과를 반드시 비교 기록한다.

### 3.3 수수료

```python
fee = notional * (maker_bps if is_maker else taker_bps) / 10_000
```

- `fee_schedule` 테이블에서 시점별 요율 조회
- 시장가·스톱 = taker, 미체결 상태로 대기 후 체결된 지정가 = maker

### 3.4 Funding

```python
# 00:00 / 08:00 / 16:00 UTC 에 포지션 보유 중이면 정산
funding_payment = position_notional_at_funding_time * funding_rate
# LONG 이고 rate > 0 이면 지급(비용), rate < 0 이면 수취
```

- 과거 funding rate 는 `funding_rates` 테이블 실제 값 사용 (추정 금지)
- `trades.funding_paid` 에 누적

### 3.5 증거금 / 강제청산

- Isolated 가정, 유지증거금률은 Binance 계단식 표를 config에 캐싱
- 강제청산 시뮬레이션: mark price 가 청산가 도달 → 포지션 전액 손실 처리 + `close_reason='liquidation'`
- 백테스트에서 청산이 **1회라도** 발생하면 그 파라미터 세트는 **실격** 처리 (리스크 설계 실패)

---

## 4. 성과 지표

### 4.1 원 명세 항목

Total Return, CAGR, Win Rate, Profit Factor, Expectancy, Max Drawdown, Sharpe, Sortino,
Average Win, Average Loss, Risk/Reward, Consecutive Losses, Monthly Return, Number of Trades

### 4.2 추가 필수 항목

| 지표 | 왜 필요한가 |
|---|---|
| **Cost / Gross PnL 비율** | 비용이 총이익의 몇 %인가. 50% 넘으면 전략이 아니라 거래소 후원 |
| **평균 R 배수 (Expectancy in R)** | 금액이 아닌 R 기준이어야 사이징과 무관하게 전략을 평가 가능 |
| **t-statistic of mean return** | Sharpe 만 보면 표본 수를 무시하게 됨 |
| **Deflated Sharpe Ratio (DSR)** | 여러 파라미터를 시도한 뒤의 Sharpe는 과대평가됨. 시도 횟수 보정 |
| **Sharpe 부트스트랩 95% CI** | 점추정만 보고 판단하지 않기 위해 |
| **Exposure time %** | 포지션 보유 시간 비율. 낮으면 Sharpe가 부풀려짐 |
| **Turnover / 월 거래횟수** | 비용 민감도와 직결 |
| **Ulcer Index / Max DD Duration** | DD 깊이만큼 지속기간도 중요 |
| **MAE/MFE 분포** | 손절폭·익절폭 적정성 판단의 유일한 근거 |
| **거래 수 (n)** | **n < 100 이면 모든 지표에 "통계적 근거 부족" 경고를 출력** |

### 4.3 기준선 비교 (항상 함께 출력)

1. Buy & Hold (동일 기간, 수수료 반영)
2. 랜덤 진입 1000회 시뮬 (동일 거래 횟수·평균 보유시간) → 결과 분포의 백분위
3. 각 feature 단독 전략
4. 등가중 가중치 (w0)

리포트에는 **"전략이 랜덤 분포의 몇 백분위인가"** 를 반드시 표시한다.
95백분위 미만이면 통계적으로 구분 불가로 간주한다.

---

## 5. 검증 프로토콜 (15번 요구사항)

### 5.1 데이터 분할

```
|---------- TRAIN ----------|-- VALIDATION --|--------- TEST (봉인) ---------|
 2019-01 ~ 2022-12            2023-01~2023-12   2024-01 ~ 현재
```

| 구간 | 용도 | 접근 횟수 |
|---|---|---|
| TRAIN | feature 설계, edge_table 추정, 초기 파라미터 | 무제한 |
| VALIDATION | 파라미터 후보 비교, 구조 결정 | 제한적 (시도 횟수 기록) |
| TEST | **최종 1회만** | **1회** |

TEST 구간을 여러 번 보면 그것은 더 이상 out-of-sample 이 아니다.
`backtest_runs.split='test'` 인 실행 횟수를 DB로 세고, 2회 이상이면 리포트에 경고를 출력한다.

### 5.2 Walk-Forward Analysis

```
[train 12m][test 3m]
      [train 12m][test 3m]
            [train 12m][test 3m]
                  ...
```

- 각 창에서 train 으로 파라미터 선택 → 다음 3개월 test 성과만 집계
- **purge + embargo**: train 종료와 test 시작 사이에 보유기간 최대치만큼 공백을 둔다
  (겹치는 포지션으로 인한 정보 누출 방지)
- 창별 성과의 **일관성**을 본다. 한두 창의 대박으로 전체 성과가 만들어지면 실패로 간주
- 지표: 창별 수익의 중앙값, 양의 창 비율, 최악 창

### 5.3 Regime 분해

`backtest/regimes.py` 에서 규칙 기반 라벨링 (사후 판단 금지 — causal 정의):

| Regime | 정의 (causal) |
|---|---|
| `trend_up` | 200일 EMA 위 + EMA 기울기 > 0 |
| `trend_down` | 200일 EMA 아래 + 기울기 < 0 |
| `range` | 위 둘 모두 아님 |
| `high_vol` | ATR 백분위 > 70 |
| `low_vol` | ATR 백분위 < 30 |

각 regime × 성과 표를 출력한다. 한 regime에서만 수익이 나면:
- 그 regime을 탐지해 그때만 거래하는 것이 맞는지, 아니면 우연인지 판단
- regime별 거래 수가 적으면(n<50) **판단 보류**

### 5.4 강건성 테스트 (전부 통과해야 다음 단계 진행)

| 테스트 | 기준 |
|---|---|
| **비용 2배** | 수수료·슬리피지 2배로 재실행 시 여전히 양의 기댓값 |
| **파라미터 섭동** | 주요 파라미터 ±20% 변경 시 성과가 급락하지 않음 (성과 표면이 평탄) |
| **시작일 이동** | 시작일을 ±30일 이동해도 결론 동일 |
| **1일 지연** | 신호를 1봉 늦게 실행해도 부호가 뒤집히지 않음 (누출 탐지) |
| **신호 셔플** | 신호를 무작위 순열로 섞으면 성과가 사라짐 (누출 탐지) |
| **심볼 교차** | ETHUSDT 등 다른 심볼에서도 방향성 일관 (완전 무관하면 BTC 과적합 의심) |

> **1일 지연 테스트와 신호 셔플 테스트는 look-ahead 누출을 잡아내는 실질적 수단이다.**
> 코드 리뷰만으로는 누출을 놓친다.

---

## 6. 백테스트-라이브 일치성 검증

### 6.1 Parity Test (자동)

```
1. 라이브 운영 중 모든 MarketSnapshot 을 직렬화해 저장 (또는 snapshot_digest 로 재구성)
2. 매일 배치로 그 스냅샷들을 백테스트 코어에 재입력
3. 산출된 Decision 과 라이브가 실제로 남긴 Decision 을 비교
4. 불일치 1건이라도 발생 → critical 알림
```

이것이 "백테스트와 실거래의 높은 일치성"을 **주장이 아니라 측정**으로 만드는 방법이다.

### 6.2 Shadow Backtest (자동, 일일)

라이브와 동일 기간에 대해 백테스트를 돌려 다음을 비교:

| 비교 항목 | 허용 오차 |
|---|---|
| 진입/청산 시점 | 0 (완전 일치해야 함) |
| 체결가 | 실측 슬리피지 차이만 허용 → 차이를 기록해 슬리피지 모델 보정 |
| 수수료 | 0 |
| funding | 0 |
| 최종 PnL | 슬리피지 차이 누적분 |

시점 불일치는 **로직 분기**를 의미하므로 즉시 조사 대상이다.

---

## 7. 리포트 출력

`backtest/report.py` 는 실행마다 다음을 생성:

```
reports/{run_id}/
  summary.json          # 전체 지표 (기계 판독용)
  summary.md            # 사람이 읽는 요약
  equity_curve.png
  drawdown.png
  monthly_returns.png
  trade_distribution.png  # R 배수 히스토그램
  mae_mfe_scatter.png
  regime_table.md
  correlation_matrix.md   # feature signal 상관관계
  baselines.md            # buy&hold / random / single-feature 비교
  robustness.md           # §5.4 결과
  veto_breakdown.md       # 거부 사유 분포
  trades.csv
```

`summary.json` 에는 `config_hash`, `git_sha`, `data_range`, `split`, `n_trades` 가 항상 포함된다.

---

## 8. 진행 게이트 (Phase 4 종료 조건)

다음을 **모두** 만족해야 Paper Trading 으로 진행한다. 하나라도 실패하면 진행하지 않는다.

- [ ] TEST 구간에서 비용 반영 후 양의 기댓값
- [ ] 거래 수 n ≥ 100
- [ ] 랜덤 기준선 분포의 95백분위 초과
- [ ] Buy & Hold 대비 위험조정수익(Sharpe) 우위
- [ ] 비용 2배 테스트 통과
- [ ] 파라미터 섭동 테스트 통과
- [ ] 1일 지연 / 신호 셔플 테스트에서 성과 소멸 확인 (누출 없음)
- [ ] Walk-forward 창의 60% 이상에서 양의 수익
- [ ] 강제청산 발생 0회
- [ ] Max DD ≤ 설정 한도

> 이 게이트를 통과하지 못하는 것이 **정상적인 결과**다.
> 그 경우 전략 가설을 바꿔서 다시 시도하는 것이지, 게이트를 낮추는 것이 아니다.
