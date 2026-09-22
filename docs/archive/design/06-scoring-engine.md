> STATUS: HISTORICAL / NOT CURRENT
> 보관일: 2026-09-23 · 원래 경로: `docs/phase0/06-scoring-engine.md`
> 승인된 문서 정리 직전 작업본. D22 및 대체된 결정을 현행 사양으로 사용하지 않는다.
> 본문은 보존하고 Markdown 상대 링크만 새 위치에 맞췄다. 현재 기준: [CURRENT_SPEC](../../current/CURRENT_SPEC.md).

# 06 — Scoring Engine 설계 (H)

> 이 문서의 설계는 원 명세의 "모든 요소를 점수화해서 가중합" 구조를 **의도적으로 수정**했다.
> 수정 이유는 [12-critique.md](12-critique.md) §2, §4 에 있다. 먼저 읽기를 권한다.

> **D22 변경 (2026-09-22):** 아래 B1/B2/T1/T2/T3 설계는 기각된 H1의 구현 이력이다.
> 현재 전략 범위는 [28-btc-multitimeframe-strategy.md](../drafts/D22-btc-multitimeframe-strategy.md)가
> 우선한다. D22의 숫자 가중치가 확정되기 전까지 아래 기존 공식을 새 전략에 재사용하지 않는다.

## 0. D22 점수 엔진의 확정된 골격

```text
마감봉 8종
  ├─ 단기  : 15m·30m·1h·2h·4h
  ├─ 중기  : 12h·1d
  └─ 장기  : 1d·1w
          ↓
MA/EMA 추세 기반
  + RSI·MACD·일목·피보나치·볼린저밴드
  + 컵앤핸들·상승/하락 다이버전스·시장구조
          ↓
4h 이상 Elliott 최종 확인
          ↓
뉴스·거시 및 JEV 보조 판단
          ↓
final_long_score / final_short_score / confidence / NO_TRADE
```

1d는 중기·장기 공통 입력이지만 한 번만 계산하고 중복 가산하지 않는다. MA/EMA가 기반이며,
나머지 지표는 계열별 확인 근거다. 점수 충돌, 데이터 결측, 뉴스 불확실성은 억지 방향 선택이
아니라 신뢰도 하락 또는 `NO_TRADE`로 표현한다.

반대 점수 역전 청산은 유지한다. 청산 후 60분 동안 신규 진입을 막고, 그 뒤 첫 마감
15분봉에서 모든 시간대·지표·뉴스 점수를 다시 계산한다. 정확한 가중치와 임계값은 Phase 0
후속 명세에서 고정한다.

## 1. 2층 구조 — Bias / Trigger 분리

원 명세의 의도("4h=추세, 1h=방향, 15m=구조, 5m=진입, 1m=타이밍")를 살리되,
**시간 스케일이 다른 신호를 하나의 가중합으로 뭉개지 않는다.**

```
        느린 층 (Bias)                        빠른 층 (Trigger)
   ─────────────────────────            ─────────────────────────
   4h 추세, 1d 추세                      15m 구조, 5m 조건, 1m 타이밍
   변동성 국면                            단기 모멘텀, 거래량 확인
   (MVP 이후) 거시 국면
           │                                       │
           ▼                                       ▼
   bias_direction ∈ {-1, 0, +1}            trigger ∈ [-1, +1]
   bias_multiplier ∈ [0, 1]
           │                                       │
           └──────────────┬────────────────────────┘
                          ▼
              direction = bias_gate × trigger
              (bias 와 방향이 다른 trigger 는 0 으로 죽인다)
```

### 왜 곱셈인가

가중합이면 "4h 추세는 하락인데 1m 모멘텀이 아주 강해서 합계가 롱"이 될 수 있다.
이것은 사용자가 말한 "4h = 전체 시장 추세" 라는 의도와 정반대다.
곱셈(게이팅)은 상위 시간대의 거부권을 구조적으로 보장한다.

```python
if bias_direction == 0:
    allowed = cfg.allow_counter_bias_when_neutral   # 기본 False
    direction = trigger * 0.5 if allowed else 0.0
elif sign(trigger) == bias_direction:
    direction = trigger
else:
    direction = 0.0        # 상위 추세 역행 진입 금지
```

`allow_counter_bias_when_neutral` 같은 스위치는 **config로 끌 수 있게 하되 기본은 보수적으로** 둔다.

---

## 2. Feature → Signal 변환

### 2.1 파이프라인

```
raw feature  ──normalize──▶  z or percentile  ──squash──▶  signal ∈ [-1, +1]
```

```python
# features/normalize.py — look-ahead 누출의 실제 발생 지점
def causal_zscore(series: Sequence[float], lookback: int) -> float:
    """as_of 시점까지의 데이터만 사용. 전체 표본 통계 사용 금지."""
    window = series[-lookback:]           # 미래 데이터 물리적으로 접근 불가
    mu, sd = mean(window), stdev(window)
    return 0.0 if sd == 0 else (series[-1] - mu) / sd

def squash(z: float, k: float = 1.5) -> float:
    """[-1,+1] 로 압축. 이상치 지배 억제."""
    return round(z / (k + abs(z)), 9)     # tanh 가 아니다 — 아래 참조
```

> **왜 `tanh` 가 아닌가**: `tanh` 는 초월함수라 libm 구현에 따라 최하위 비트가 달라질 수 있다.
> 개발(Windows)과 운영(Linux)이 다른 값을 내면 Parity Test(07 §6.1)가 무의미해진다.
> `z/(k+|z|)` 는 사칙연산만 쓰므로 IEEE 754 상 모든 플랫폼에서 비트 단위로 같다.
> 형태도 유사하며, 입력이 `k` 일 때 출력이 정확히 0.5 다.
> 출력은 9자리로 양자화해 미세 차이가 하위 계층에 전파되지 않게 한다.

**금지 사항 (테스트로 강제)**
- `MinMaxScaler`/`StandardScaler` 를 전체 데이터에 fit 하는 것
- 전략 파라미터를 전체 기간 통계로 정하는 것
- 미래 봉을 참조하는 중심화 이동평균, repainting ZigZag

### 2.2 MVP feature 목록 (의도적으로 작게)

**총 5개로 시작한다.** 이유: 3년 데이터에서 독립적인 거래 표본은 수백 건 수준인데,
파라미터가 수십 개면 통계적으로 아무것도 검증할 수 없다. (→ 12-critique §2)

#### Bias 층 (2개)

| ID | 정의 | TF | 근거 |
|---|---|---|---|
| `B1_trend` | EMA(50) 대비 가격 위치 + 기울기, **ATR 단위** | 4h | 가장 널리 문서화된 암호화폐 추세 지속 효과 |
| `B2_volregime` | ATR(14)/Close 의 1년 백분위 | 1h | 국면 분류. 방향 신호가 아니라 게이트/사이즈용 |

> ⚠ **B1 에는 z-score 를 쓰지 않는다** (2026-09-21 구현 중 정정).
> z-score 는 "최근 이력 대비 이례적인가" 를 재므로, 지속 추세에서 분산이 줄어 0 에 수렴한다.
> 그러면 강한 추세장에서 bias 게이트가 열리지 않는다. ATR 단위 정규화가 맞다.
> z-score 는 T2(되돌림 종료 = 이례적 움직임 탐지)에만 쓴다.

```python
bias_direction = sign(B1_trend) if |B1_trend| >= cfg.bias_threshold else 0
bias_risk_scale = f(B2_volregime)   # 정상 1.0, 고변동 0.5 — **사이징에만** 적용 (D14-2)
                                    # 극단 국면은 리스크 게이트가 별도로 차단한다
```

#### Trigger 층 (3개)

| ID | 정의 | TF | 근거 |
|---|---|---|---|
| `T1_pullback` | 추세 방향 기준 되돌림 깊이 (RSI(14) 또는 최근 스윙 대비 피보나치 위치) | 15m | "추세 중 눌림목 재개" 가설의 핵심 |
| `T2_momentum` | 직전 N봉 수익률의 causal z | 5m | 되돌림 종료 확인 |
| `T3_volume` | 거래량 / 거래량 SMA(20) 의 causal z | 5m | 참여 확인. 단독 방향성 없음 → T1/T2 부호에 곱해지는 **확인 계수** |

```python
# D14 — 순서가 있는 구조. 가중합이 아니다.
setup   = T1_pullback 되돌림이 최근 setup_lookback_bars 안에 있었고 무효화되지 않았는가
trigger = squash(T2_momentum_z, k) * confirm(T3_volume)   if setup else 0.0
# squash(x, k) = x / (k + |x|)  — tanh 가 아니다 (플랫폼 결정론)
```

여기서 `T2_momentum` 은 **압축 전 causal z-score** 다 (D13). 압축은 한 번만 한다.

`T3` 를 가중합에 더하지 않고 곱하는 이유: 거래량 자체는 방향이 없다. 가중합에 넣으면
"거래량이 많으니 롱" 같은 무의미한 기여가 생긴다.

#### 왜 T1 을 더하지 않고 조건으로 두는가 (D14-1, 실측 근거)

2026-09-21 실데이터 스윕(2,184회 평가)에서 **진입 16건 중 12건의 T1 신호가 정확히 0**
이었다. 진입을 만든 것은 전적으로 T2 의 z-spike 였다.

원인은 두 지표가 **같은 봉에서 동시에 성립할 수 없기** 때문이다. T1 은 RSI 가 눌림
구간(롱 기준 35~50)에 있을 때 양수인데, T2 z 가 +2 를 넘으려면 직전 30분 수익률이
이례적으로 커야 하고 그러면 RSI 는 이미 눌림 구간을 벗어나 있다. 전 구간 Spearman 은
+0.37 로 §2.3 기준(0.7)을 통과하지만 **진입 조건 근처의 꼬리에서는 상호배타적**이다 —
상관계수 하나로는 잡히지 않는 종류의 문제다.

가설 H1(D11)이 말한 것은 원래 **순서**였다: 되돌림이 먼저, 재개가 나중.
그래서 T1 은 점수가 아니라 조건이 되고, T2 가 그 뒤의 방아쇠가 된다.

#### 왜 국면 계수를 direction 에 곱하지 않는가 (D14-2, 실측 근거)

`squash` 출력은 유계이므로, 70점 진입선은 `|direction| >= 0.40` 을 요구한다.
앞에 곱해지는 계수 `m` 이 있으면 필요한 합성값은 `1.5·r/(1−r)`, `r = 0.4/m` 로 급격히 커진다.

| 곱해지는 계수 | 필요한 pre-squash 값 | 필요한 T2 z-score |
|---|---|---|
| 1.0 (정상 · 강한 거래량) | 1.0 | 1.0 |
| 0.8 (T3 보통 확인) | 1.5 | 2.0 |
| 0.6 (T3 약한 확인) | 3.0 | 5.0 |
| 0.5 (B2 고변동 국면) | 6.0 | **11.0 — 사실상 불가능** |

실측에서 전체 봉의 **48% 가 고변동 국면**이었고 진입 16건은 **전부 normal 국면**에서
나왔다. 즉 `bias_multiplier = 0.5` 는 "신호를 절반으로 줄인다" 가 아니라 **"거래를
금지한다"** 로 작동했다. §2.2 표가 B2 를 "게이트/사이즈용" 이라고 적은 것과도 어긋난다.

**확정 (D14-2)**: 국면 계수는 `risk_scale` 로 **사이징의 거래당 허용 손실에만** 곱한다.
거래를 막아야 하는 극단 국면은 리스크 게이트 G5.1/G5.2 가 **명시적으로** 차단한다.
완충(크기 축소)과 차단(거래 금지)을 서로 다른 장치가 맡는다.

> **일반 규칙**: 유계 함수의 출력 뒤에 고정 임계값이 있으면, 그 앞에 곱해지는 어떤
> 계수도 완충장치가 아니라 **거부권**이 된다. 완충하려면 임계값을 통과한 *다음* 단계
> (크기)에 적용해야 한다.

### 2.3 상관관계 제약

feature 추가 시 다음을 반드시 통과해야 한다:

```
train 구간에서 각 signal 쌍의 |Spearman corr| < 0.7
```

RSI / MACD / ROC / 모멘텀은 서로 0.8 이상 상관인 경우가 흔하다.
이들을 모두 넣으면 **하나의 신호를 4번 카운트**하는 것이며, 신호 대비 노이즈를 늘리는 결과가 된다.

상관관계 행렬은 백테스트 리포트에 **항상 출력**한다.

---

## 3. 점수 산출

### 3.1 단일 방향성 값에서 파생

```python
direction        # [-1, +1]  — 위 §1 의 결과
long_score  = 50.0 * (1.0 + direction)      # 0..100
short_score = 100.0 - long_score            # 0..100
```

**`long_score` 와 `short_score` 를 독립 계산하지 않는다.**
독립 계산하면 `long=80, short=75` 같은 해석 불가능한 상태가 생긴다.
"양쪽 근거가 다 강함"은 방향 점수가 아니라 **confidence 가 낮은 것**으로 표현해야 한다.

### 3.2 confidence_score

원 명세에 정의가 없어 다음과 같이 정의한다.

```python
agreement = 1.0 - dispersion_of_component_signs   # 0..1
            # 각 컴포넌트 signal 부호의 가중 일치도

data_quality = min over TF of freshness_score     # 0..1
regime_stability = 1.0 - recent_regime_flip_rate  # 0..1  (최근 N봉 국면 전환 빈도)
magnitude = min(1.0, |direction| / cfg.confidence_full_at)   # 기본 0.6

confidence_score = 100 * (agreement ** 0.5) * data_quality * regime_stability * magnitude
```

**confidence 의 용도는 제한한다**: 사이징 조절 + 로깅.
진입 여부 판단에 confidence 임계값을 추가하면 자유도가 또 늘어난다. MVP 에서는 사이징에만 쓴다.

### 3.3 risk_score

```python
risk_score = 100 * clip(
    0.5 * vol_percentile
  + 0.2 * spread_percentile
  + 0.2 * event_proximity          # 블랙아웃 창에 얼마나 가까운가 0..1
  + 0.1 * funding_extremity,
    0, 1)
```

soft 값이다. hard 차단은 Risk Engine 게이트가 담당한다 (→ 05 문서 §1.2).

---

## 4. 진입 / 청산 정책

```yaml
policy:
  long_entry_threshold:  70       # long_score >= 70  -> 롱 진입 후보
  short_entry_threshold: 70       # short_score >= 70
  reversal_requires_flat: true    # 반대 진입은 반드시 청산 후 별도 판단
```

```
long_score
   100 ┤
    70 ┤━━━━━━━━━ 진입선 ━━━━━━━━━━
       │         ↑ 진입           ↓ 유지 구간 (아무 동작 없음)
    50 ┤━━━━━━━━━ 중립선 ━━━━━━━━━━
       │         ↓ short_score가 long_score를 추월하면 롱 청산
     0 ┤
```

신호 청산은 임계값 설정이 아니라 **두 방향 점수의 상대 비교**다 (D12).

- LONG 보유: `short_score > long_score` 이면 `SIGNAL` 청산
- SHORT 보유: `long_score > short_score` 이면 `SIGNAL` 청산
- 동점(`50 == 50`)은 유지한다. 반대 점수가 엄격히 커져야 한다.
- 실현손익의 부호와 무관하게 닫는다. 수익이면 성과 집계상 익절이지만 `close_reason` 은 항상 `SIGNAL` 이다.

추가 규칙 (전부 config):
- `min_hold_minutes` — 반대 진입 제한 시간 (Risk Engine G4.5). 신호 청산은 막지 않음
- `reentry_cooldown_minutes` — 청산 후 재진입 대기 (G4.6)
- `same_direction_limit` — 동일 방향 연속 진입 제한 (G4.7)
- `daily_trade_limit`, `consecutive_loss_limit` (G3.3/G3.5)
- **반전(reversal) 금지**: 롱→숏 직접 전환 대신 롱 청산 → 쿨다운 → 숏 재판단.
  직접 전환은 수수료 2배 + 판단 오류 시 손실 2배

---

## 5. 비용 게이트 (`strategy/cost.py`)

### 5.1 비용 계산

```python
expected_cost_bps = (
    entry_fee_bps                      # taker 기본 (시장가 진입 가정)
  + exit_fee_bps
  + 2 * expected_slippage_bps          # 진입 + 청산
  + expected_funding_bps               # 예상 보유기간 × funding rate
)
```

- 수수료율: `fee_schedule` 테이블 (거래소 API `commissionRate`) — 하드코딩 금지
- 슬리피지: `base_bps + a * vol_percentile + b * (notional / recent_volume)`
  계수 `a`, `b` 는 **Phase 8 실측 체결로 보정** (그 전까지는 보수적 상수)
- funding: 현재 rate × 예상 보유 정산 횟수. 방향에 따라 부호가 다름

참고 (Binance USD-M, VIP 0, 2026년 기준 확인 필요):
- taker ≈ **4.5 bps**, maker ≈ **2.0 bps**
- 시장가 왕복 = 9 bps + 슬리피지 2~6 bps ≈ **11~15 bps**
- 이는 **BTC가 왕복 0.11~0.15% 이상 유리하게 움직여야 본전**이라는 뜻이다

### 5.2 expected_edge — 여기가 가장 조심해야 할 부분

```python
expected_edge_bps = conditional_mean_return_bps(
    score_bucket = bucket(direction),
    regime       = snapshot.regime,
    horizon      = cfg.expected_holding_bars,
    table        = edge_table,          # 백테스트에서 추정한 룩업 테이블
)
```

**순환 논리 위험**: `edge_table` 을 같은 데이터로 추정하고 그 데이터로 백테스트하면
비용 게이트가 항상 통과하는 자기충족 구조가 된다.

규칙:
1. `edge_table` 은 **train 구간에서만** 추정한다
2. validation/test 백테스트는 train에서 만든 테이블을 **고정**해서 쓴다
3. 각 버킷의 표본 수를 함께 저장하고, `n < 30` 인 버킷은 `edge = 0` 으로 처리 (fail-closed)
4. `edge_table` 은 `config_versions` 에 포함되어 해시에 반영된다

### 5.3 최종 게이트

```python
if expected_edge_bps <= expected_cost_bps + cfg.minimum_required_edge_bps:
    return Veto(INSUFFICIENT_EDGE)
```

`minimum_required_edge_bps` 기본 제안: **5 bps**
(= 비용 추정 오차에 대한 안전 마진. 비용 11~15bps 대비 약 40%)

### 5.4 비용이 설계에 주는 함의

| 목표 보유기간 | 왕복 비용 | 필요 승률 (RR=1) | 현실성 |
|---|---|---|---|
| 수 분 | ~13 bps | 매 거래 0.13% 이상 순수익 | 매우 어려움 |
| 수 시간 | ~13 bps + funding | 시간당 변동 대비 감당 가능 | 현실적 |
| 수 일 | ~13 bps + funding 다회 | 비용 비중 작음 | 현실적 |

→ **목표 보유기간을 먼저 정해야 한다.** (→ 12-critique 미결사항 §2)
1분봉 평가 주기는 "타이밍 해상도"이지 "보유기간"이 아니다. 둘을 혼동하면 비용에 잡아먹힌다.

---

## 6. 가중치 정책

| 버전 | 방식 | 언제 |
|---|---|---|
| **w0** | 등가중 (1/n) | MVP 기준선. **이것을 이기지 못하면 나머지는 무의미** |
| w1 | 도메인 지식 기반 수동 지정 | Phase 4에서 w0와 비교 |
| w2 | walk-forward 최적화 + 정규화 페널티 | Phase 10 이후, out-of-sample 개선이 잡음 수준을 넘을 때만 |

**w0 등가중을 기준선(baseline)으로 삼는 것이 핵심이다.**
최적화된 가중치가 등가중을 out-of-sample에서 이기지 못하면, 그 최적화는 노이즈 적합이다.

추가 기준선 (백테스트에서 항상 함께 계산):
- Buy & Hold
- 랜덤 진입 (동일 거래 횟수·동일 보유시간, 1000회 시뮬)
- 단일 feature 전략 (각 feature 하나씩)

전략이 이 넷을 모두 이기지 못하면 **복잡도가 정당화되지 않는다.**

---

## 7. Elliott Wave 처리 (D22에서 핵심 범위로 변경)

D22에서는 **4h 이상 Elliott 파동을 최종 구조 확인으로 구현한다.** 단, 아래의
repainting 방지 규칙을 먼저 만족해야 하며 단독 진입 신호로 사용하지 않는다.

대신 재현 가능하고 repainting 하지 않는 부분만 `features/structure.py` 에 남긴다:

| 포함 | 제외 |
|---|---|
| ZigZag swing 검출 (확정된 스윙만 — 확정 지연을 명시적으로 모델링) | Wave 1~5 카운팅 |
| HH/HL/LH/LL 구조 라벨 | ABC 보정 식별 |
| 최근 스윙 기준 피보나치 되돌림 위치 (0.382/0.5/0.618) | 다중 시나리오 confidence |
| 스윙 기반 구조적 손절 위치 | invalidation rule 자동 판정 |

**ZigZag 의 repainting 문제**: 임계값 `d`% ZigZag 에서 마지막 스윙은 가격이 반대로 `d`% 움직여야
확정된다. 즉 **스윙 확정은 항상 지연된다.** 백테스트에서 "지금 보이는 스윙"을 그대로 쓰면
미래 정보를 쓰는 것이다.

→ 구현 규칙: `swing_confirmed_at_ms` 를 함께 반환하고, `SnapshotBuilder` 는
`swing_confirmed_at_ms <= as_of_ms` 인 스윙만 노출한다. 이는 단위 테스트 대상이다.

파동 카운팅 구현 규칙:
- 시나리오는 복수 유지, 각각 confidence
- 전체 `direction` 기여도 상한 **±0.15** (config)
- Elliott on/off 백테스트 비교에서 out-of-sample 개선이 없으면 제거

---

## 8. 뉴스 / 거시의 scoring 반영 (D22 핵심 범위)

| 요소 | D22 최소 역할 | 확장 역할 |
|---|---|---|
| 경제 캘린더 | **블랙아웃 게이트** (G6) | 검증 가능한 vintage consensus 확보 시 방향 기여 |
| 뉴스 | 고중요 이벤트 `NO_TRADE`·사이즈 축소 | 제한된 LONG/SHORT 기여 |
| FOMC 문서 AI/JEV 분석 | 모델·프롬프트·입력 기록 | 검증 후 제한된 방향 기여 |

뉴스 방향 점수의 상한은 아직 미확정이다. 과거 사건의 결말을 알고 있는 모델로 만든
백테스트는 진짜 검증으로 인정하지 않으며, 검증 불가능한 입력은 우선 게이트·축소처럼
보수적인 방향으로만 작동시킨다.

검증할 수 없는 요소가 **수익을 만든다**고 주장하는 것은 위험하지만,
검증할 수 없는 요소가 **위험할 때 거래를 줄인다**는 것은 보수적 방향이므로 허용 가능하다.

---

## 9. 결정 함수 전체 흐름

```python
def decide(snapshot, portfolio, cfg) -> Decision:
    dq = assess_data_quality(snapshot)
    if not dq.ok:
        return Decision.veto([VetoReason.DATA_QUALITY], dq=dq)

    feats   = compute_features(snapshot, cfg)          # causal 보장
    bias    = compute_bias(feats, cfg)
    trigger = compute_trigger(feats, cfg)
    scores  = compose_scores(bias, trigger, feats, cfg)

    action  = policy.resolve(scores, portfolio, cfg)   # 진입 임계값 / 점수 역전 청산
    if action is Action.NONE:
        return Decision.no_action(scores)

    if action.is_exit_like():                          # 청산/스톱조정은 리스크 게이트 통과 불필요
        return Decision.act(action, scores, intents=build_exit_intents(...))

    cost    = estimate_cost(snapshot, cfg)
    edge    = estimate_edge(scores, snapshot, cfg.edge_table)
    if edge <= cost + cfg.minimum_required_edge_bps:
        return Decision.veto([VetoReason.INSUFFICIENT_EDGE], scores, edge, cost)

    proposal = build_proposal(action, scores, snapshot, cfg)
    verdict  = risk.evaluate(proposal, portfolio, risk_state, snapshot, cfg.risk)
    if verdict.is_veto:
        return Decision.veto(verdict.reasons, scores, edge, cost)

    qty = sizing.compute(proposal, portfolio, snapshot, cfg.risk, verdict.size_multiplier)
    return Decision.act(action, scores, intents=build_entry_intents(proposal, qty))
```

**모든 반환 경로가 `Decision` 을 남긴다.** "아무 것도 안 함"도 기록된다.
거부 사유의 분포는 전략 진단의 1차 자료다 (예: 90%가 `INSUFFICIENT_EDGE` 면 설계를 다시 봐야 한다).
