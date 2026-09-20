# 05 — Risk Engine 설계 (G)

## 1. 설계 원칙

### 1.1 Risk Engine 은 "거부권"만 가진다

```python
# 개념 시그니처 (순수 함수)
def evaluate(
    proposal: TradeProposal,      # 전략이 하고 싶은 것
    portfolio: PortfolioState,
    risk_state: RiskState,
    snapshot: MarketSnapshot,
    cfg: RiskConfig,
) -> RiskVerdict:                 # approve | approve_with_size | veto(reasons)
    ...
```

- Risk Engine 은 **진입을 막을 수 있지만 만들 수 없다**
- Risk Engine 은 **청산을 막을 수 없다** — 안전 방향 동작은 게이트를 통과하지 않는다
- 게이트는 전부 순수 함수. 각각 단위 테스트 가능
- 하나라도 veto 면 전체 veto. 모든 veto 사유를 수집해서 기록 (첫 번째에서 중단하지 않음 — 진단 가치)

### 1.2 전략과 리스크의 역할 분리

| | Scoring Engine 의 `risk_score` | Risk Engine 의 게이트 |
|---|---|---|
| 성격 | 연속값 (soft) | 이진 (hard) |
| 영향 | 포지션 **크기** 축소 | 진입 **차단** |
| 우회 가능 | 가중치로 조절 가능 | 불가 |
| 예 | 변동성이 다소 높음 → 사이즈 0.6배 | ATR 백분위 > 98 → 진입 금지 |

두 개념을 섞으면 "점수를 올려서 리스크 한도를 뚫는" 경로가 생긴다. 반드시 분리한다.

### 1.3 Fail-closed

게이트 판단에 필요한 데이터가 없거나 stale 하면 **veto** 한다.
예: 변동성 게이트가 ATR을 필요로 하는데 캔들에 갭이 있으면 → `veto(DATA_QUALITY)`.

---

## 2. 게이트 목록

평가 순서는 "싸고 확실한 것 먼저". 모든 항목은 config로 on/off 및 값 조정 가능.

### G1 — 시스템 게이트

| ID | 조건 | 기본값 | veto 사유 코드 |
|---|---|---|---|
| G1.1 | 시스템 상태가 RUNNING 이 아님 | — | `SYSTEM_NOT_RUNNING` |
| G1.2 | 킬스위치 활성 | — | `KILLSWITCH` |
| G1.3 | UNKNOWN 상태 주문 존재 | — | `UNRESOLVED_ORDER` |
| G1.4 | 코드가 dirty (uncommitted) 인데 mode=live | — | `DIRTY_CODE` |
| G1.5 | 거래소 시각 drift > 임계 | 1000ms | `CLOCK_DRIFT` |
| G1.6 | 최근 reconcile 실패 | — | `RECONCILE_FAILED` |

### G2 — 데이터 품질 게이트

| ID | 조건 | 기본값 | 코드 |
|---|---|---|---|
| G2.1 | 필요한 TF 중 하나라도 마지막 봉이 stale | tf 길이 × 1.5 | `STALE_DATA` |
| G2.2 | warm-up 미충족 (feature별 `warmup_bars`) | — | `WARMUP` |
| G2.3 | 최근 N봉 내 미해결 갭 존재 | 200봉 | `CANDLE_GAP` |
| G2.4 | 가격 이상 감지 (직전 대비 \|Δ\| > threshold, 또는 O/H/L/C 관계 위반) | 10% / 1m | `BAD_PRICE` |
| G2.5 | 호가 스프레드 과대 | 5 bps | `WIDE_SPREAD` |
| G2.6 | mark price 와 last price 괴리 과대 | 50 bps | `PRICE_DIVERGENCE` |

### G3 — 계좌 / 손실 게이트

| ID | 조건 | 기본 제안값 | 코드 |
|---|---|---|---|
| G3.1 | 일일 실현+미실현 손실 ≥ 한도 | equity의 **2%** | `DAILY_LOSS_LIMIT` |
| G3.2 | 최고점 대비 누적 DD ≥ 한도 | **10%** | `MAX_DRAWDOWN` |
| G3.3 | 연속 손실 횟수 ≥ 한도 | **4회** | `CONSECUTIVE_LOSSES` |
| G3.4 | 쿨다운 진행 중 (연속 손실 후) | **4시간** | `COOLDOWN` |
| G3.5 | 일일 거래 횟수 ≥ 한도 | **2회** [확정] | `DAILY_TRADE_LIMIT` |
| G3.6 | 가용 증거금 부족 (버퍼 포함) | 필요증거금 × 1.5 | `INSUFFICIENT_MARGIN` |

> 기본값은 **합리적 출발점이며 최적값이 아니다.**
> 특히 G3.1/G3.2 는 사용자의 위험 감수 성향에 따라 결정되어야 하며,
> 백테스트로 "최적화"할 대상이 아니다 (최적화하면 반드시 느슨해진다).

### G4 — 포지션 / 노출 게이트

| ID | 조건 | 기본 제안값 | 코드 |
|---|---|---|---|
| G4.1 | 이미 동일 심볼 포지션 보유 (피라미딩 비허용) | — | `POSITION_EXISTS` |
| G4.2 | 계산된 명목가 > 최대 명목가 | equity × **1.00** [확정] (= margin 10% × lev 10) | `MAX_NOTIONAL` |
| G4.3 | 거래소 레버리지 > 최대 | **10x** [확정] (Phase 8 은 3x) | `MAX_LEVERAGE` |
| G4.4 | 계산 수량 < 거래소 최소 (minQty / minNotional) | — | `BELOW_MIN_SIZE` |
| G4.5 | 최소 포지션 유지시간 미경과 상태에서 반대 진입 | **120분** [확정] | `MIN_HOLD_TIME` |
| G4.6 | 청산 후 재진입 대기시간 미경과 | **60분** [확정] | `REENTRY_COOLDOWN` |
| G4.7 | 동일 방향 재진입 제한 (같은 방향 연속 N회) | **2회** | `SAME_DIRECTION_LIMIT` |
| G4.8 | 청산가가 손절가보다 가까움 | `liq_distance >= 2 × stop_distance` | `LIQUIDATION_TOO_CLOSE` |
| **G4.9** | **최대 보유시간 초과 → 강제 청산 트리거** | **48시간** [확정] | `MAX_HOLDING_TIME` |
| **G4.10** | **수량 반올림 오차 > 허용치 (자본 부족)** | **5%** [확정] | `SIZING_RESOLUTION` |
| **G4.11** | **손절가가 진입가의 이익 방향에 있음 (손절거리 ≤ 0)** | — | `INVALID_STOP` |
| **G4.12** | **전략 손절이 최소 손절거리보다 좁음** | **1%** [확정] | `STOP_TOO_TIGHT` (SKIP 모드일 때만) |

**G4.8 은 협상 불가 항목이다.** 손절에 닿기 전에 강제청산되면 리스크 모델 전체가 무의미해진다.
확정 설정(최대 10x)에서는 청산까지 10.7% 로 1% 손절의 10.7배라 사실상 걸리지 않지만,
Binance 유지증거금률이 명목가에 따라 계단식으로 오르므로 게이트는 유지한다 (§3.5).

**G4.9 는 게이트가 아니라 청산 트리거다.** 다른 게이트와 달리 진입을 막는 것이 아니라
청산을 *발생시킨다*. 보유기간 2~24h 를 넘긴 포지션의 2차 탈출 경로다 (D2).

**G4.10 은 자본 부족을 조용히 넘기지 않기 위한 장치다.** `step_size` 반올림으로 실제 명목가가
목표에서 5% 이상 벗어나면 진입을 거부하고 경고한다 (→ 13-decisions.md D3, 05 §3.4).

### G5 — 시장 상태 게이트

| ID | 조건 | 기본 제안값 | 코드 |
|---|---|---|---|
| G5.1 | ATR 백분위(1h, 1년 롤링) > 상한 | **98%** | `VOL_TOO_HIGH` |
| G5.2 | ATR 백분위 < 하한 (수수료 대비 움직임 부족) | **5%** | `VOL_TOO_LOW` |
| G5.3 | 최근 N분 내 가격 스파이크 | 3분 내 3% | `RECENT_SPIKE` |
| G5.4 | funding rate 절댓값 극단 | \|rate\| > 0.1%/8h | `EXTREME_FUNDING` |
| G5.5 | funding 정산 임박 + 불리한 방향 | 정산 5분 전 | `FUNDING_IMMINENT` |

### G6 — 이벤트 블랙아웃 게이트 (MVP에 포함 — 일정만 있으면 되므로)

| 이벤트 | 진입 금지 창 (기본 제안) |
|---|---|
| FOMC 금리 결정 | 발표 전 **60분** ~ 후 **30분** |
| CPI / Core CPI | 전 **30분** ~ 후 **15분** |
| PCE / Core PCE | 전 **20분** ~ 후 **15분** |
| Nonfarm Payroll | 전 **30분** ~ 후 **15분** |
| Powell 기자회견 | 시작 ~ 종료 + **15분** |

- 값은 전부 config
- **"후" 창도 반드시 둔다.** 발표 직후 몇 분은 스프레드 확대·급반전이 잦아 손절 슬리피지가 커진다
- 캘린더 데이터를 가져오지 못하면 → `fail-closed` 로 할지 `fail-open` 으로 할지 선택 필요.
  권장: **알려진 FOMC 일정은 하드 데이터로 캐싱**하고, 소스 장애 시 캐시 사용. 캐시도 만료되면 `veto`

### G7 — 비용 게이트

전략 쪽 `strategy/cost.py` 에서 계산하지만, 최종 확인은 Risk Engine 이 한 번 더 한다.
→ [06-scoring-engine.md](06-scoring-engine.md) §5

---

## 3. 포지션 사이징 — 손절 우선, 레버리지는 결과값

> **확정 방식: 위험 기반 사이징 (`risk_based`)** — [13-decisions.md](13-decisions.md) **D9**
> D4 의 고정 명목가 방식을 **대체**한다. 거래당 위험 1% 는 그대로이며, 명목가를 고정하는 대신
> **전략이 정한 손절 위치**에서 역산한다.

### 3.0 계산 순서 (레버리지를 먼저 정하지 않는다)

```
전략 신호
  ↓
[1] entry_price 결정
  ↓
[2] stop_price 결정            ← 전략이 먼저. 레버리지와 무관
      Swing Low/High, 지지·저항, ATR, 피보나치 무효화 수준 등
  ↓
[3] stop_distance_pct 계산     ← 항상 양수
  ↓
[4] max_loss = equity × RISK_PER_TRADE
  ↓
[5] 비용 추정 (진입·청산 수수료, 슬리피지, funding)
  ↓
[6] position_notional 계산     ← 비용 포함 닫힌 해
  ↓
[7] 상한 적용 (명목가 / 레버리지 / 증거금 / 거래소)
  ↓
[8] qty = floor_to_step(notional / price)
  ↓
[9] exchange_leverage 결정     ← 정수. 증거금 상한을 만족하는 최소값
  ↓
[10] 청산가 안전성 검증
  ↓
[11] 거래소 최소 주문 검증
  ↓
[12] Risk Engine 최종 승인 → 주문
```

**핵심**: "몇 배를 쓸까"를 먼저 묻지 않는다.
`손절 위치 → 허용 손실 → 포지션 크기 → 필요 레버리지` 순서다.

### 3.1 손절 거리

```python
# 항상 양수
if side is PositionSide.LONG:
    stop_distance_pct = (entry_price - stop_price) / entry_price
else:
    stop_distance_pct = (stop_price - entry_price) / entry_price

if stop_distance_pct <= 0:
    return Veto(VetoReason.INVALID_STOP)      # 손절이 진입가의 이익 방향에 있음
```

**최소 손절 거리** (`MIN_STOP_DISTANCE`, 확정 **0.01 = 1%**)

> ⚠ **이 값은 "이만큼 손실이 날 때까지 버틴다"는 뜻이 아니다.**
> 최소 손절 거리는 **포지션 크기를 계산하는 분모**일 뿐이다 (§3.2).
> 점수가 청산선 아래로 내려가면 손실이 0.01% 든 0% 든 **즉시 청산한다.**
> 두 경로의 역할은 다음과 같다.
>
> | 경로 | 언제 | 성격 |
> |---|---|---|
> | **신호 청산** (`long_score <= 45`) | 봉 마감마다 평가 | **주 탈출 경로.** 손실 크기와 무관 |
> | 최대 보유시간 (48h) | 시간 초과 | 2차 탈출 경로 |
> | **손절 주문** (`stop_price`) | 가격이 손절가 도달 | **backstop.** 프로세스가 죽어도 작동 |
>
> 즉 `MIN_STOP_DISTANCE` 를 낮추면 "더 늦게 자른다"가 아니라 **"포지션이 커진다"** 가 된다.

전략 손절이 이보다 좁으면 `TIGHT_STOP_ACTION` 에 따라 처리한다.

| 값 | 동작 |
|---|---|
| `WIDEN` (기본) | `MIN_STOP_DISTANCE` 로 넓힌다. 명목가가 줄어 더 안전해진다. 발생 횟수를 기록 |
| `SKIP` | 거래를 건너뛴다 |

> 1% 라는 값의 근거는 §3.6 에 있다. **임의의 값이 아니라 비용 구조와 레버리지 상한에서
> 역산된 값**이다. 비용 구조가 바뀌면 이 값도 다시 계산해야 한다.

#### MVP 손절 위치 규칙 (`stop.mode = "structure_or_atr"`)

손절 위치를 정하는 것은 전략의 일이지만, MVP 규칙은 여기에 고정한다.

```python
# LONG
structure_stop = last_confirmed_swing_low - swing_buffer_atr * atr   # 기본 0.2 x ATR
atr_stop       = entry_price - atr_multiple * atr                    # 기본 1.5 x ATR(1h)
stop_price     = min(structure_stop, atr_stop)      # 더 먼 쪽 = 더 보수적

# SHORT 은 부호 반전, max() 사용
```

**더 먼 쪽을 고르는 이유**: 손절이 멀수록 명목가가 작아지고, 명목가가 작을수록
스톱 슬리피지 사고의 피해가 작아진다 (§3.8). 손실 한도(1%)는 어느 쪽이든 같으므로
**더 먼 손절이 언제나 더 안전하다.**

**스윙은 확정된 것만 쓴다** (`swing_confirmed_at_ms <= as_of_ms`).
형성 중인 스윙을 쓰면 repainting 이 발생해 백테스트를 신뢰할 수 없다 (06 §7).

#### ⚠ 확정 지연 실측 (2026-09-21, 1시간봉 1년)

| ZigZag 임계 | 스윙 수 | 평균 확정지연 | 최대 |
|---|---|---|---|
| 1% | 2,493 | 2.5시간 | 46시간 |
| **2% (확정값)** | **630** | **6.3시간** | **70시간** |
| 3% | 260 | 13.6시간 | 94시간 |
| 5% | 88 | 38.3시간 | 219시간 |

**함의**: 보유기간이 2~24시간(D2)인데 "가장 최근 확정 스윙"이 평균 6시간, 최대 70시간 전의
것일 수 있다. 따라서 구조적 손절은 현재가에서 **최소 2% 이상** 떨어지게 되고,
ATR 손절(1.5×ATR(1h) ≈ 0.6%)보다 거의 항상 넓다.

`pick: wider` 규칙에 따라 **구조적 손절이 사실상 항상 채택된다.**
결과적으로 실제 손절폭은 2~4% 수준이 되고, `min_stop_distance = 1%` 는 거의 발동하지 않으며
실효 레버리지도 상한 10x 보다 훨씬 낮아진다 (손절 2%면 5x).

이것은 안전한 방향이지만 의도한 설계인지 Phase 4 에서 확인해야 한다.
→ 미해결 항목으로 등록 (PROGRESS.md)

### 3.2 명목가 — 비용 포함 닫힌 해

비용을 무시한 단순식은 `N = L / s` 지만, 이는 **수수료·슬리피지만큼 위험 예산을 초과**한다.
정확히 풀면:

```
L      = equity × RISK_PER_TRADE          (최대 허용 손실)
s      = stop_distance_pct
c_in   = taker_fee + slippage_entry       (진입 비용률)
c_out  = taker_fee + slippage_stop        (청산 비용률, 스톱은 슬리피지 배수 적용)
f      = expected_funding_rate            (예상 보유기간 동안, 명목가 대비)

조건:  N·s  +  N·c_in  +  N·(1+s)·c_out  +  N·f  ≤  L
        └가격손실┘  └진입비용┘   └청산비용┘      └funding┘

풀면:
       N = L / [ s·(1 + c_out) + c_in + c_out + f ]
```

```python
denom = (
    stop_distance_pct * (Decimal(1) + cost_rate_out)
    + cost_rate_in
    + cost_rate_out
    + expected_funding_rate
)
position_notional = safe_div(max_loss, denom)
```

**청산 수수료에 `(1+s)` 를 쓰는 이유**: 수수료는 *청산 시점의* 명목가에 부과된다.
LONG 은 손절 시 명목가가 `N·(1−s)` 로 줄고, SHORT 은 `N·(1+s)` 로 는다.
양쪽 모두에 `(1+s)` 를 적용하면 LONG 은 약간 과대추정(보수적), SHORT 은 정확하다.
**보수적인 쪽으로 통일**한다.

#### 검산 (equity 1,000 / risk 1% / entry 100,000 / stop 98,000)

| 항목 | 값 |
|---|---|
| `L` | 10.00 USDT |
| `s` | 2.00% |
| `c_in` | 0.065% (taker 0.045% + slip 0.020%) |
| `c_out` | 0.085% (taker 0.045% + stop slip 0.040%) |
| 분모 | 0.02×1.00085 + 0.00065 + 0.00085 = **0.021517** |
| **명목가** | 10 / 0.021517 = **464.7 USDT** |
| 가격 손실 | 464.7 × 0.02 = 9.29 |
| 진입 비용 | 464.7 × 0.00065 = 0.30 |
| 청산 비용 | 464.7 × 0.98 × 0.00085 = 0.39 |
| **손절 시 총손실** | **9.98 USDT** ≤ 10 ✅ |

비용을 무시했다면 `N = 10/0.02 = 500`, 실제 손실은 10.75 USDT 로 **예산을 7.5% 초과**했을 것이다.

### 3.3 증거금과 레버리지 — 개념 정정 (중요)

> **`MARGIN_ALLOCATION` 은 손실을 제한하지 않는다.**

손절 시 손실은 `명목가 × 손절폭` 으로 결정되며 **증거금과 무관**하다.
증거금이 결정하는 것은 **청산가까지의 거리** 하나뿐이다.

| 개념 | 정의 | 무엇을 결정하나 |
|---|---|---|
| `max_loss` | equity × RISK_PER_TRADE | **거래당 손실 한도** |
| `position_notional` | max_loss / (s + 비용) | **실제 시장 노출**. 손실과 비용이 여기 비례 |
| `margin` | notional / leverage | **청산가 거리**. 손실 크기와 무관 |
| `leverage` | notional / margin | 위 둘의 비율. **입력이 아니라 결과** |

따라서 `MARGIN_ALLOCATION` 은 **"목표"가 아니라 "증거금 상한"** 으로 쓴다.
증거금이 많을수록(레버리지가 낮을수록) 청산가가 멀어져 안전하므로,
**상한 안에서 가능한 한 많이** 넣는 것이 옳다.

```python
margin_cap = equity * MARGIN_ALLOCATION            # 확정 0.10

# 증거금 상한을 만족하는 "최소" 정수 레버리지.
# 레버리지를 낮출수록 증거금이 늘어 청산가가 멀어지므로, 가능한 가장 낮은 값을 쓴다.
exchange_leverage = max(1, ceil(position_notional / margin_cap))

margin_allocated = position_notional / exchange_leverage   # <= margin_cap 보장
effective_leverage = position_notional / equity            # 계좌 전체 기준 (보고용)
```

거래소 레버리지는 **정수**이므로 `required_leverage` 를 구해 반올림하지 않고,
위처럼 **올림(ceil)으로 직접 구한다**. 내림하면 증거금 상한을 넘는다.

### 3.4 상한 검증

```python
if exchange_leverage > MAX_LEVERAGE:                 # 확정 10
    if LEVERAGE_LIMIT_ACTION == "REDUCE_POSITION":   # 기본
        position_notional = margin_cap * MAX_LEVERAGE
        exchange_leverage = MAX_LEVERAGE
        # -> 실제 위험이 예산보다 작아진다 (안전). 단 §3.6 의 경고를 볼 것
    else:  # "SKIP_TRADE"
        return Veto(VetoReason.MAX_LEVERAGE)

# 수량 확정 (항상 내림)
qty = floor_to_step(position_notional / entry_price, step_size)
actual_notional = qty * entry_price                  # 반올림으로 목표보다 작아짐

# 반올림 후 재계산 — 보고값은 "의도"가 아니라 "실제"여야 한다
estimated_loss_at_stop = (
    actual_notional * stop_distance_pct
    + actual_notional * cost_rate_in
    + actual_notional * (1 + stop_distance_pct) * cost_rate_out
    + actual_notional * expected_funding_rate
)
assert estimated_loss_at_stop <= max_loss            # 내림했으므로 항상 성립

if qty < min_qty or actual_notional < min_notional:
    return Veto(VetoReason.BELOW_MIN_SIZE)

rounding_error = abs(actual_notional - position_notional) / position_notional
if rounding_error > MAX_ROUNDING_ERROR:              # 확정 0.05
    return Veto(VetoReason.SIZING_RESOLUTION)
```

### 3.5 청산가 안전성 (G4.8)

```python
# Isolated margin 근사. mmr 은 거래소 계단식 유지증거금률 (leverageBracket 에서 조회)
liq_distance_pct = Decimal(1) / exchange_leverage - maintenance_margin_rate

if liq_distance_pct < LIQUIDATION_SAFETY_BUFFER * stop_distance_pct:   # 확정 2.0
    # 레버리지를 낮추면 청산가가 멀어진다. 증거금 상한을 넘으면 거래를 포기한다.
    return Veto(VetoReason.LIQUIDATION_TOO_CLOSE)
```

현재 확정값에서 이 게이트는 **사실상 걸리지 않는다**:

| 레버리지 | 청산까지 | 2% 손절 대비 |
|---|---|---|
| 3x | 32.9% | 16.5배 |
| **5x (상한)** | **19.6%** | **9.8배** |
| 10x | 9.6% | 4.8배 |
| 20x | 4.6% | 2.3배 |

실질적인 제약은 `MAX_LEVERAGE` 다. 그래도 이 게이트를 유지하는 이유:
① Binance 유지증거금률은 명목가가 커지면 **계단식으로 상승**한다
② 설정이 바뀌어 레버리지가 20x 를 넘으면 그때는 실제로 걸린다
③ 방어는 겹쳐 두는 것이 원칙이다

### 3.6 ⚠ 파라미터 3개는 독립적이지 않다

`RISK_PER_TRADE`, `MARGIN_ALLOCATION`, `MAX_LEVERAGE` 는 **명목가 상한을 통해 묶여 있다**.

```
명목가 상한 = MARGIN_ALLOCATION × MAX_LEVERAGE × equity
            = 0.10 × 10 × equity = equity의 100%

이 상한에 걸리기 시작하는 손절폭:
  s* = RISK_PER_TRADE / (MARGIN_ALLOCATION × MAX_LEVERAGE) − (c_in + c_out)
     = 0.01 / 1.00 − 0.0015
     = 0.85%
```

**즉 손절폭이 0.85% 보다 좁으면 레버리지 상한에 무조건 걸린다.**
그러면 `REDUCE_POSITION` 이 상시 발동해 **체계적으로 예산보다 적게 위험을 진다**.
이는 안전하지만, 백테스트의 R 분포를 왜곡해 전략 평가를 어렵게 만든다.

→ **`MIN_STOP_DISTANCE = 1%` 는 여기서 나온 값이다** (0.85% 보다 약간 위). 세 파라미터와 정합시킨 결과다.

#### 설정 검증 (config/schema.py 에서 강제)

기동 시 다음을 계산하고, 불일치하면 **경고 후 기동 중단**한다.

```python
implied_min_stop = RISK_PER_TRADE / (MARGIN_ALLOCATION * MAX_LEVERAGE) - (c_in + c_out)
# 0.01 / (0.10 * 10) - 0.0015 = 0.85%
if MIN_STOP_DISTANCE < implied_min_stop:
    raise ConfigError(
        f"MIN_STOP_DISTANCE({MIN_STOP_DISTANCE:.2%}) 가 "
        f"레버리지 상한이 함의하는 최소 손절폭({implied_min_stop:.2%}) 보다 작다. "
        f"MAX_LEVERAGE 를 {RISK_PER_TRADE / (MARGIN_ALLOCATION * MIN_STOP_DISTANCE):.1f} "
        f"이상으로 올리거나 MIN_STOP_DISTANCE 를 올려야 캡이 상시 발동하지 않는다."
    )
```

#### 다른 조합을 쓰고 싶다면

| 원하는 최소 손절폭 | 필요한 MAX_LEVERAGE (MARGIN 10% 기준) |
|---|---|
| 3% | 3.3 이상 |
| 2% | 5 이상 |
| **1% (확정)** | **10 이상** |
| 0.5% | 20 이상 |

### 3.7 손절폭에 따른 사이징 표 (equity 1,000 / risk 1%)

| 손절폭 | 명목가 | 계좌대비 | 거래소 레버리지 | 왕복 비용 | 비용/예산 | 청산까지 | 청산/손절 | 캡 |
|---|---|---|---|---|---|---|---|---|
| 0.5% | 1,537 | 154% | 16x | 2.30 | **23.0%** | 5.9% | 11.8x | **걸림** |
| **1.0%** (하한) | **869** | **87%** | **9x** | **1.30** | **13.0%** | 10.7% | 10.7x | — |
| 1.5% | 606 | 61% | 7x | 0.90 | 9.0% | 13.9% | 9.3x | — |
| 2.0% | 465 | 46% | 5x | 0.69 | 6.9% | 19.6% | 9.8x | — |
| 3.0% | 317 | 32% | 4x | 0.47 | 4.7% | 24.6% | 8.2x | — |
| 5.0% | 194 | 19% | 2x | 0.28 | 2.8% | 49.6% | 9.9x | — |
| 10.0% | 98 | 10% | 1x | 0.14 | 1.4% | 99.6% | 10.0x | — (= D4) |

**읽는 법 세 가지**

1. **좁은 손절은 비싸다.** 1% 손절은 위험 예산의 13%를 비용으로 쓴다 (2% 손절은 6.9%).
   손실 한도는 같지만 **비용이 두 배**이므로 그만큼 이길 확률이 낮아진다.
2. **좁은 손절은 명목가를 키운다.** 1% 손절이면 명목가가 계좌의 87% 다.
   손절이 제값에 체결되지 않을 때의 피해가 그만큼 커진다 (§3.8).
3. **10% 손절 행이 곧 기존 D4 설정이다.** 명목가 98 ≈ equity의 10%, 레버리지 1x.
   즉 이 방식은 D4 를 폐기하는 것이 아니라 **일반화한 것**이다.

청산 안전 게이트(G4.8)는 이 범위 전체에서 여유가 크다 (최소 8.2배). 실질 제약은 `MAX_LEVERAGE` 다.

### 3.8 "1% 손실"은 보장이 아니라 목표다

`STOP_MARKET` 은 **시장가 체결**이다. 지정한 가격에 체결된다는 보장이 없다.

| 상황 | 결과 |
|---|---|
| 정상 | 손실 ≈ 예산의 100% 이하 ✅ |
| 급락·유동성 공백 | 스톱 가격을 지나쳐 체결 → **예산 초과** |

**그리고 이 초과분은 명목가에 비례한다.** 이것이 이 설계에서 가장 중요한 실거래 위험이다.

```
BTC 가 스톱을 5% 지나쳐 체결된 경우의 추가 손실:
  D4 방식     (명목가  98 = equity  10%):   4.9 USDT  (equity의 0.5%)
  D9 / 2% 손절 (명목가 465 = equity  46%):  23.3 USDT  (equity의 2.3%)   ←  4.7배
  D9 / 1% 손절 (명목가 869 = equity  87%):  43.5 USDT  (equity의 4.4%)   ←  8.9배
```

즉 **명목가가 커진 만큼 슬리피지 꼬리위험도 커진다.** 이를 통제하는 장치는:

| 장치 | 역할 |
|---|---|
| `MAX_LEVERAGE` | 명목가 상한 = equity의 100%. **이것이 갭 위험의 실질적 상한** |
| `G5.1 변동성 상한` | 슬리피지가 큰 국면에서 진입 금지 |
| `daily_loss_limit` / `max_drawdown` | 사후 방어 |
| Phase 8 실측 | 실제 슬리피지를 측정해 `slippage_stop` 계수 보정 |

→ **Phase 8(소액 실거래) 은 `MAX_LEVERAGE = 3` 으로 시작**하고, 실측 슬리피지를 확인한 뒤
Phase 9 에서 단계적으로(3 → 5 → 10) 올린다. 검증 전에 명목가를 키우면 손실만 커진다.
`MAX_LEVERAGE = 10` 은 **설계상의 천장**이지 Phase 8 의 출발값이 아니다.

### 3.9 레버리지는 결과값이지 입력값이 아니다

```
effective_leverage = actual_notional / equity          # 계좌 전체 기준 (보고용)
exchange_leverage  = ceil(notional / margin_cap)       # 거래소 설정값 (정수)
```

거래소 레버리지 설정은 **"허용 상한"** 일 뿐 사이징에 쓰지 않는다.
포지션 진입 전에 `POST /fapi/v1/leverage` 로 설정하며, **설정 실패 시 진입하지 않는다.**
포지션 보유 중에는 변경이 제한되므로 반드시 진입 전에 수행한다.

### 3.10 size_multiplier — MVP 에서는 1.0 고정 (확정)

가변 사이징은 [13-decisions.md](13-decisions.md) D1 에 따라 MVP 에서 제외되었다.
구현은 남기되 기본값은 `1.0` 이다.

```python
# Phase 9 이후 A/B 검증 대상 (MVP 미사용)
size_multiplier = clip(bias_multiplier * confidence_factor * risk_factor, 0.25, 1.0)
max_loss = equity * RISK_PER_TRADE * size_multiplier
```

### 3.11 Stop / Take Profit / Trailing

| 항목 | 설계 |
|---|---|
| Stop Loss | 진입 체결 즉시 `STOP_MARKET` + `closePosition=true`, `workingType=MARK_PRICE`. **전략이 정한 가격** |
| Take Profit | `TAKE_PROFIT_MARKET` + `closePosition=true`. **R 배수 기준** (손절폭이 가변이므로). 기본 2R |
| Trailing | **MVP 제외.** Phase 14 |
| 최대 보유시간 | 48시간 초과 시 강제 청산 (G4.9) |

> 손절폭이 가변이 되었으므로 익절도 **% 고정이 아니라 R 배수**로 되돌린다.
> 2% 손절이면 익절 +4%, 5% 손절이면 익절 +10%. 일관된 손익비가 유지된다.

### 3.12 기록 항목

진입 직전 아래 값을 `decisions.sizing_json` 에 저장하고, 체결 후 `positions` 에 반영한다.
Telegram 진입 알림에도 요약을 포함한다.

```json
{
  "account_equity": "1000.00",
  "risk_per_trade": "0.01",
  "max_loss": "10.00",
  "entry_price": "100000",
  "stop_price": "98000",
  "stop_distance_pct": "0.02",
  "stop_source": "swing_low",
  "stop_widened": false,
  "cost_rate_in": "0.00065",
  "cost_rate_out": "0.00085",
  "expected_funding_rate": "0.0000",
  "target_notional": "464.75",
  "actual_notional": "464.00",
  "qty": "0.00464",
  "margin_cap": "100.00",
  "margin_allocated": "92.80",
  "exchange_leverage": 5,
  "effective_leverage": "0.464",
  "estimated_loss_at_stop": "9.97",
  "liquidation_price": "80320",
  "liq_distance_pct": "0.1968",
  "liq_buffer_ratio": "9.84",
  "leverage_capped": false,
  "rounding_error": "0.0016",
  "risk_approved": true,
  "veto_reasons": []
}
```

우리 스키마 대응 (03-data-model.md):

| 저장 위치 | 항목 |
|---|---|
| `decisions.sizing_json` (신규 컬럼) | 위 JSON 전체 |
| `positions.planned_risk_usdt` | `estimated_loss_at_stop` |
| `positions.initial_stop_price` | `stop_price` |
| `positions.leverage_used` | `exchange_leverage` |
| `trades.realized_r` | `net_pnl / planned_risk_usdt` — 손절폭이 가변이므로 **R 배수가 유일하게 비교 가능한 척도** |

> D4 에서 `realized_r_stop` / `realized_r_atr` 을 나눠 기록하기로 했던 것은 **철회**한다.
> 손절폭이 전략적으로 결정되므로 `planned_risk_usdt` 하나로 R 이 제대로 정의된다.

---

## 4. 리스크 상태 관리

### 4.1 일일 리셋

- 기준 시각: **UTC 00:00** (funding 정산 시각과 정렬)
- 리셋 항목: `day_realized_pnl`, `day_trade_count`
- 리셋 **안 하는** 항목: `consecutive_losses`, `equity_high_water`, `drawdown_pct`

### 4.2 Drawdown 계산

```
equity_high_water = max(equity_high_water, current_equity)   # DB에 영속화
drawdown_pct = (equity_high_water - current_equity) / equity_high_water
```

`equity_high_water` 를 메모리에만 두면 재시작 시 DD가 0으로 리셋되어 한도가 무력화된다.
반드시 `account_snapshots` 에 저장하고 기동 시 복원한다.

### 4.3 HALTED 해제

- **자동 해제 없음.** 사람이 Telegram 명령 또는 CLI로 명시적 해제
- 해제 전 필수 확인 사항을 알림에 포함: 원인, 현재 포지션, 손실 규모
- 단, `DEGRADED` 는 원인 해소 시 자동 복귀

---

## 5. 킬 스위치

세 가지 발동 경로:

| 경로 | 구현 | 지연 |
|---|---|---|
| 파일 | `data/KILL` 파일 존재 여부를 매 루프 검사 | ≤ 1분 |
| Telegram | `/kill` 명령 | ≤ 5초 |
| 워치독 | 불변식 위반 감지 시 자동 | ≤ 5초 |

동작 모드 (config):
- `block_entries` (기본): 신규 진입만 차단
- `flatten`: 신규 차단 + 전 포지션 시장가 청산

> `flatten` 을 기본값으로 두지 않는 이유: 일시적 데이터 이상으로 포지션이 강제 청산되면
> 그 자체가 손실이다. 보호주문이 이미 걸려 있으므로 `block_entries` 로 충분한 경우가 대부분이다.

---

## 6. 확정 설정값 (config/base.yaml)

> **확정 출처**: [13-decisions.md](13-decisions.md) D2 / **D9**. `[확정]` 표시가 없는 값은 잠정 초안이다.

```yaml
risk:
  # --- 사이징 (D9: 손절 우선, 레버리지는 결과값) --------------------------
  sizing_mode: "risk_based"           # [확정] D4 의 fixed_notional_pct 를 대체
  risk_per_trade: 0.01                # [확정] 거래당 최대 손실 = equity * 1%
  margin_allocation: 0.10             # [확정] 증거금 "상한" (목표 아님, §3.3)
  max_leverage: 10                    # [확정] Phase 8 에서는 3 으로 시작 (§3.8)
  min_stop_distance: 0.01             # [확정] §3.6 에서 역산된 값. 임의값 아님
  max_rounding_error: 0.05            # [확정] 초과 시 SIZING_RESOLUTION veto
  liquidation_safety_buffer: 2.0      # [확정] 청산거리 >= 2 x 손절거리

  leverage_limit_action: "REDUCE_POSITION"   # REDUCE_POSITION | SKIP_TRADE
  tight_stop_action: "WIDEN"                 # WIDEN | SKIP

  # --- 손절 위치 규칙 (§3.1) ---------------------------------------------
  stop:
    mode: "structure_or_atr"
    atr_timeframe: "1h"
    atr_period: 14
    atr_multiple: 1.5
    swing_buffer_atr: 0.2
    pick: "wider"                 # wider | tighter

  # 명목가 상한 = margin_allocation * max_leverage = equity 의 100%
  # 이 값이 갭·슬리피지 꼬리위험의 실질적 상한이다 (§3.8)

  # --- 비용 추정 (사이징 공식의 입력) -------------------------------------
  cost:
    taker_fee_rate: 0.00045           # 거래소 API 로 갱신. 하드코딩 아님
    maker_fee_rate: 0.00020
    slippage_entry_rate: 0.00020      # Phase 8 실측으로 보정
    slippage_stop_multiple: 2.0       # 스톱은 유동성 공백에서 체결되므로 가중
    include_funding: true

  # --- 손실 한도 ----------------------------------------------------------
  daily_loss_limit_pct: 0.02          # [확정] 손절 2회분
  max_drawdown_pct: 0.10              # [확정] 누적 DD -> HALTED
  consecutive_loss_limit: 4
  cooldown_after_losses_min: 240

  # --- 거래 빈도 / 보유 ---------------------------------------------------
  daily_trade_limit: 2                # [확정] 보유기간 2~24h 와 정합
  min_hold_minutes: 120               # [확정]
  reentry_cooldown_minutes: 60        # [확정]
  max_holding_hours: 48               # [확정] 2차 탈출 경로 (G4.9)
  same_direction_limit: 2
  max_unprotected_ms: 5000

  # --- 하드 게이트 임계값 (G2/G3/G5) ------------------------------------
  margin_availability_buffer: 1.5
  max_spread_bps: 5
  max_price_divergence_bps: 50
  recent_spike_window_min: 3
  recent_spike_pct: 0.03
  extreme_funding_rate: 0.001
  funding_imminent_min: 5

  take_profit:
    enabled: true
    mode: "r_multiple"                # [확정] 손절폭이 가변이므로 R 배수 (§3.11)
    r_multiple: 2.0                   # 잠정 — Phase 4 에서 결정

  volatility_gate:
    atr_pct_upper: 0.98
    atr_pct_lower: 0.05
    lookback_days: 365

  blackout:
    FOMC:  {before_min: 60, after_min: 30}
    CPI:   {before_min: 30, after_min: 15}
    PCE:   {before_min: 20, after_min: 15}
    NFP:   {before_min: 30, after_min: 15}

  killswitch:
    mode: "block_entries"             # block_entries | flatten

  # MVP 미사용 (구현은 유지, Phase 9+ A/B 대상)
  fixed_notional:
    enabled: false
    position_notional_pct: 0.10
```

### 설정 정합성 검증 (기동 시 강제)

`config/schema.py` 는 값의 범위뿐 아니라 **파라미터 간 관계**를 검증한다 (§3.6).

```python
implied_min_stop = risk_per_trade / (margin_allocation * max_leverage) - (c_in + c_out)
# 0.01 / (0.10 * 10) - 0.0015 = 0.85%

if min_stop_distance < implied_min_stop:
    raise ConfigError(...)      # 레버리지 캡이 상시 발동하게 되는 설정
```

이 검증이 없으면 세 파라미터를 따로 바꿨을 때 **캡이 조용히 상시 발동**하여
백테스트의 위험 분포가 왜곡된다. 설정 오타보다 발견하기 어려운 종류의 사고다.

**`[확정]` 이 아닌 값들은 검증되지 않은 출발점이다.** 백테스트 결과로 리스크 한도를
느슨하게 조정하는 것은 과최적화다. 조정은 오직 보수적인 방향으로만 한다.
