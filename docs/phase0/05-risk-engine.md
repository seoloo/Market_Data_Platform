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
| G4.2 | 계산된 명목가 > 최대 명목가 | equity × **0.15** [확정] | `MAX_NOTIONAL` |
| G4.3 | 결과 레버리지 > 최대 | **1.0x** [확정] | `MAX_LEVERAGE` |
| G4.4 | 계산 수량 < 거래소 최소 (minQty / minNotional) | — | `BELOW_MIN_SIZE` |
| G4.5 | 최소 포지션 유지시간 미경과 상태에서 반대 진입 | **120분** [확정] | `MIN_HOLD_TIME` |
| G4.6 | 청산 후 재진입 대기시간 미경과 | **60분** [확정] | `REENTRY_COOLDOWN` |
| G4.7 | 동일 방향 재진입 제한 (같은 방향 연속 N회) | **2회** | `SAME_DIRECTION_LIMIT` |
| G4.8 | 청산가가 손절가보다 가까움 | `liq_distance >= 2 × stop_distance` | `LIQUIDATION_TOO_CLOSE` |
| **G4.9** | **최대 보유시간 초과 → 강제 청산 트리거** | **48시간** [확정] | `MAX_HOLDING_TIME` |
| **G4.10** | **수량 반올림 오차 > 허용치 (자본 부족)** | **5%** [확정] | `SIZING_RESOLUTION` |

**G4.8 은 협상 불가 항목이다.** 손절에 닿기 전에 강제청산되면 리스크 모델 전체가 무의미해진다.
(확정 설정의 0.1x 레버리지에서는 자동으로 통과하지만, 설정이 바뀔 경우를 위해 유지한다.)

**G4.9 는 게이트가 아니라 청산 트리거다.** 다른 게이트와 달리 진입을 막는 것이 아니라
청산을 *발생시킨다*. 손절이 거의 발동하지 않는 확정 설정에서 2차 탈출 경로 역할을 한다 (→ §3.1c).

**G4.10 은 자본 부족을 조용히 넘기지 않기 위한 장치다.** `step_size` 반올림으로 실제 명목가가
목표에서 5% 이상 벗어나면 진입을 거부하고 경고한다 (→ 13-decisions.md D3).

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

## 3. 포지션 사이징

> **확정 방식: 고정 비율 명목가 (`fixed_notional_pct`)** — [13-decisions.md](13-decisions.md) D4
> 위험기반(`risk_based`) 방식은 §3.1b 에 참고용으로 남긴다. 두 방식 모두 구현하되
> MVP 기본값은 `fixed_notional_pct` 다.

### 3.1 확정 공식 — 고정 비율 명목가

```python
# 1. 명목가 = equity 의 고정 비율
equity   = wallet_balance + unrealized_pnl
notional = equity * cfg.position_notional_pct        # 확정 0.10

# 2. 손절가 = 진입가 대비 고정 비율
stop_price = entry_price * (1 - cfg.stop_loss_pct)   # LONG,  확정 0.10
stop_price = entry_price * (1 + cfg.stop_loss_pct)   # SHORT

# -> 거래당 최대 손실 = equity * position_notional_pct * stop_loss_pct
#                     = equity * 0.10 * 0.10 = equity * 1.0%

# 3. 수량
qty_raw = notional / price

# 4. 상한 적용
qty = min(
    qty_raw,
    (equity * cfg.max_notional_multiple) / price,   # G4.2  확정 0.15
    (equity * cfg.max_leverage) / price,            # G4.3  확정 1.0
    available_margin_based_cap,                     # G3.6
    exchange_max_qty,
)

# 5. 거래소 정밀도 (항상 내림)
qty = floor_to_step(qty, step_size)

# 6. 최소 검사
if qty < min_qty or qty * price < min_notional:
    return Veto(BELOW_MIN_SIZE)

# 7. 반올림 오차 검사 — 자본이 작으면 여기서 걸린다
rounding_error = abs(qty * price - notional) / notional
if rounding_error > cfg.max_rounding_error:          # 기본 0.05 (5%)
    return Veto(SIZING_RESOLUTION)                   # 자본 부족 경고 동반
```

**게이트 G4.10 — `SIZING_RESOLUTION`**: `step_size` 때문에 실제 명목가가 목표에서
5% 이상 벗어나면 진입을 거부한다. 자본이 작을 때 리스크 모델이 조용히 무의미해지는 것을 막는다.
(→ 13-decisions.md D3 의 최소 자본 산정과 연결)

### 3.1b 참고 — 위험 기반 방식 (MVP 미사용, 구현은 유지)

```python
risk_budget    = equity * risk_per_trade_pct          # 예: 0.5%
atr_stop       = k_atr * ATR('1h')
structure_stop = distance_to_recent_swing
stop_distance  = max(atr_stop, structure_stop, min_stop_bps * price)
qty_raw        = risk_budget / stop_distance
```

변동성에 자동 적응한다는 장점이 있으나, 손절폭이 좁아지면 레버리지가 올라가 비용이
비례 증가한다 (→ 12-critique §7). Phase 9 이후 A/B 비교 대상으로 남긴다.

### 3.1c 이 방식에서 손절의 역할이 바뀐다 (중요)

`stop_loss_pct = 0.10` 은 보유기간 2~24시간에서 **거의 발동하지 않는다.**
BTC의 24시간 내 10% 역행은 연 수 회 수준의 사건이다.

→ 손절가는 **파국 방지용 backstop** 이며, 실질적 손실 통제는 다음이 담당한다:

| 순위 | 수단 | 설정 |
|---|---|---|
| 1 | 청산 신호 (`long_score <= exit_threshold`) | 06 §4 |
| 2 | 최대 보유시간 강제 청산 | `max_holding_hours: 48` |
| 3 | 일일 손실 한도 / 누적 DD | G3.1 / G3.2 |
| 4 | 거래소 스톱 주문 (backstop) | `stop_loss_pct: 0.10` |

**설계 함의**
- `max_holding_hours` 게이트를 **반드시 구현**한다 (신규 G4.9). 이것이 2차 탈출 경로다
- `realized_r` (R 배수) 계산 시 10% 손절폭을 분모로 쓰면 R 값이 항상 0에 가까워 무의미하다.
  → `realized_r_stop`(10% 기준)과 `realized_r_atr`(2×ATR 기준) **둘 다 기록**한다
- 백테스트에서 `close_reason` 분포를 반드시 확인한다. `stop` 비율이 5%를 넘으면
  보유기간 가정이나 변동성 게이트를 재검토해야 한다

### 3.2 레버리지는 결과값이지 입력값이 아니다

```
leverage_used = (qty * price) / equity      # 확정 설정에서는 항상 약 0.1x
```

- 거래소 레버리지 설정값은 "허용 상한"일 뿐, 사이징에 사용하지 않는다
- 거래소 레버리지는 `max_leverage` 보다 약간 높게 설정하되(예: 5x), 실제 명목가는 위 공식이 결정
- 그 이유: 거래소 레버리지를 낮추면 필요 증거금이 커져 오히려 청산가가 가까워질 수 있다.
  **Isolated 모드 + 낮은 명목가 + 높은 설정 레버리지** 조합이 청산가를 가장 멀리 둔다

### 3.3 size_multiplier — MVP 에서는 1.0 고정 (확정)

가변 사이징은 [13-decisions.md](13-decisions.md) D1 에 따라 MVP 에서 제외되었다.
구현은 남기되 기본값은 `1.0` 이다.

```python
# Phase 9 이후 A/B 검증 대상 (MVP 미사용)
size_multiplier = clip(bias_multiplier * confidence_factor * risk_factor, 0.25, 1.0)
```

### 3.4 변동성 적응 부재에 대한 보완

고정 비율 명목가는 변동성에 적응하지 않는다. 같은 10% 명목가라도 고변동 국면에서는
저변동 국면보다 몇 배 큰 손익 변동을 만든다.

→ **변동성 게이트 G5.1 / G5.2 가 유일한 적응 장치**가 되므로 중요도가 올라간다.
→ 임계값(상한 98% / 하한 5%)을 Phase 4 백테스트에서 국면별 성과와 함께 재검토한다.
→ 향후 선택지: `vol_target` 오버레이 (명목가를 ATR 백분위로 ±50% 조정). Phase 9 이후.

### 3.5 Stop / Take Profit / Trailing

| 항목 | 설계 |
|---|---|
| Stop Loss | 진입 즉시 거래소에 `STOP_MARKET` + `closePosition=true`, `workingType=MARK_PRICE`. 진입가 대비 **-10%** (확정) |
| Take Profit | `TAKE_PROFIT_MARKET` + `closePosition=true`. 손절폭이 고정이므로 **% 기준** (잠정 +5%, Phase 4에서 결정) |
| Trailing | **MVP 제외.** 거래소 `TRAILING_STOP_MARKET` 대신 우리가 스톱을 갱신하는 방식을 권장 (백테스트 재현 가능하므로). Phase 9+ |
| 손익분기 이동 | 1R 도달 시 스톱을 진입가 + 비용으로 이동 — 선택적, config |

SL/TP 를 둘 다 `closePosition=true` 로 걸면 한쪽 체결 후 다른 쪽이 자동 취소되지 않는 경우가 있다.
→ 포지션이 FLAT이 되면 해당 심볼의 잔여 reduce-only 주문을 **명시적으로 취소**한다 (INV-5 보호).

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

> **확정 출처**: [13-decisions.md](13-decisions.md) D2 / D4. `[확정]` 표시가 없는 값은 잠정 초안이다.

```yaml
risk:
  sizing_mode: "fixed_notional_pct"   # [확정]
  position_notional_pct: 0.10         # [확정] 명목가 = equity * 10%
  stop_loss_pct: 0.10                 # [확정] 진입가 대비 -10%
  # -> 거래당 최대 손실 = equity * 1.0%

  max_leverage: 1.0                   # [확정] 설계는 0.1x, 상한은 1.0 으로 방어
  max_notional_multiple: 0.15         # [확정]
  max_rounding_error: 0.05            # [확정] 초과 시 SIZING_RESOLUTION veto

  daily_loss_limit_pct: 0.02          # [확정] 손절 2회분
  max_drawdown_pct: 0.10              # [확정] 누적 DD -> HALTED
  consecutive_loss_limit: 4
  cooldown_after_losses_min: 240

  daily_trade_limit: 2                # [확정] 보유기간 2~24h 와 정합
  min_hold_minutes: 120               # [확정]
  reentry_cooldown_minutes: 60        # [확정]
  max_holding_hours: 48               # [확정] 2차 탈출 경로 (G4.9)
  same_direction_limit: 2

  liquidation_buffer_multiple: 2.0
  max_unprotected_ms: 5000

  take_profit:
    enabled: true
    mode: "pct"                       # [확정] R배수 대신 % (손절폭 고정이므로)
    take_profit_pct: 0.05             # 잠정 — Phase 4 에서 결정

  volatility_gate:                    # 고정 사이징이라 중요도 높음 (§3.4)
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
  risk_based:
    enabled: false
    risk_per_trade_pct: 0.005
    k_atr: 2.0
    atr_timeframe: "1h"
    min_stop_bps: 15
```

**`[확정]` 이 아닌 값들은 검증되지 않은 출발점이다.** 백테스트 결과로 리스크 한도를
느슨하게 조정하는 것은 과최적화다. 조정은 오직 보수적인 방향으로만 한다.
