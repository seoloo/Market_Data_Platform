# 13 — Phase 0 확정 결정 기록

확정일: 2026-09-20
상태: **D1·D2·D6~D11 확정 / D3·D5 보류 / D4 는 D9 로 대체**

이 문서가 Phase 1 이후 구현의 기준이다. 여기 없는 것은 확정되지 않은 것이다.

---

## D1. MVP 범위 — 12-critique.md 권고 5가지 전부 수용 ✅

| # | 결정 | 영향 문서 |
|---|---|---|
| 1 | feature **5개**, 가중치 **등가중 고정** | 06 §2.2, §6 |
| 2 | Elliott Wave 파동 카운팅 **MVP 제외** — swing 구조 + 피보나치만 | 06 §7 |
| 3 | 뉴스/LLM **Phase 11로 이관**, 진입 신호가 아니라 **게이트 용도로만** | 06 §8, 08 §4 |
| 4 | 거시경제 **캘린더 블랙아웃만** (G6). surprise 스코어링은 consensus 자체 적립 후 | 05 G6, 08 §3 |
| 5 | 보유기간 재정의 (→ D2) | 06 §5.4 |

→ [10-roadmap.md](10-roadmap.md) §3 의 MVP 제외 목록이 그대로 확정 범위다.

---

## D2. 목표 보유기간 — 수 시간 ~ 1일 ✅

| 항목 | 값 |
|---|---|
| 목표 보유기간 | **2시간 ~ 24시간** |
| 예상 연 거래 수 | **100 ~ 250건** |
| 전략 평가 주기 | 1분 (해상도) — 보유기간과 구분 |
| 최대 보유시간 (강제 청산) | **48시간** (config `max_holding_hours`) |

### 파생되는 설정 변경

| 설정 | 기존 초안 | 확정 |
|---|---|---|
| `daily_trade_limit` | 6 | **2** (연 250건 상한과 정합) |
| `min_hold_minutes` | 30 | **120** |
| `reentry_cooldown_minutes` | 15 | **60** |
| `max_holding_hours` | (없음) | **48** — 신규 |

`daily_trade_limit` 을 2로 낮춘 이유: 보유기간 2~24시간에서 하루 6회 진입은 구조적으로 불가능하다.
한도는 **이상 동작 탐지용 안전장치**여야지, 실제로 도달 가능한 값이면 안 된다.

`max_holding_hours` 를 신설한 이유: D4의 손절 구조(아래) 때문에 손절이 거의 발동하지 않으므로,
**시간 기반 청산이 실질적인 2차 탈출 경로**가 된다.

---

## D3. 운용 자본 — 미정 (Phase 4 게이트 통과 후 결정) ⏸

Phase 1~4(데이터 · 전략코어 · 백테스트 · 검증)는 자본 규모와 무관하게 진행한다.

### Phase 8 착수 전에 확인해야 할 최소 자본 (**D9** 기준)

D9 에서 명목가는 손절폭에 따라 달라지므로, **가장 불리한 경우(넓은 손절)** 로 계산한다.
손절폭 10% 일 때 명목가가 가장 작아진다 (≈ equity × 10%):

```
equity × 0.10  >=  max(min_notional, step_size × price)
```

손절폭 2% 일 때는 명목가가 equity × 46% 이므로 제약이 훨씬 느슨하다.
**좁은 손절만 잡히는 구간에서는 소액으로도 거래가 되지만, 넓은 손절이 나오면 주문이 거부된다.**
이 비대칭이 조용한 사고가 되지 않도록 G4.10 `SIZING_RESOLUTION` 게이트가 잡는다.

| 제약 | Binance BTCUSDT (런타임 조회) |
|---|---|
| `min_notional` | 100 USDT |
| `step_size` | 0.001 BTC |
| 실질 최소 명목가 | `max(100, 0.001 × BTC가격)` |

- **1스텝도 못 내는 하한**: `equity < 10 × 최소명목가` → 거래 자체 불가
- **사이징 해상도 확보선**: 명목가가 최소단위의 **10배 이상**이어야 수량 반올림 오차가 1% 미만
  → 권장 `equity >= 100 × 최소명목가`

BTC 가격이 높을수록 `step_size × price` 가 커져 필요 자본이 올라간다.
정확한 값은 `exchangeInfo` + 현재가로 **런타임 계산**하며, 기동 시 미달이면 경고를 띄운다.

> 자본이 부족하면 위험기반이든 고정비율이든 사이징이 무의미해진다.
> 그 경우 Paper Trading 단계를 길게 가져가는 편이 낫다 (→ 10-roadmap Phase 6).

---

## D4. 리스크 한도 — 고정 비율 명목가 방식 ⚠️ **D9 로 대체됨**

> **이 절의 사이징 방식은 [D9](#d9-포지션-사이징--손절-우선-레버리지는-결과값--확정-d4-사이징-방식을-대체) 로 대체되었다.**
> 유지되는 것: **거래당 최대 손실 = equity 1%**, 계좌 누적 DD −10%, 일일 손실 2%.
> 대체되는 것: 명목가 10% 고정 → 손절폭에서 역산. 손절 −10% 고정 → 전략이 결정.
> 아래 내용은 판단 근거 보존을 위해 남긴다.

### 지정하신 값 (사용자 확인 완료)

> "매매 액수를 총액 10%로 하고, 최대 손절은 -10%.
>  100달러면 매매할 때 10달러로 한다. 거기서 -10%, 즉 1달러를 손해 보면 손절한다.
>  최종 손실은 총액 100달러의 1%이므로 99달러가 된다."

### 확정 해석

| 파라미터 | 값 | 의미 |
|---|---|---|
| `position_notional_pct` | **0.10** | 포지션 명목가 = equity(지갑잔고 + 미실현) × 10% |
| `stop_loss_pct` | **0.10** | 명목가 대비 **10% 손실** = 진입가 대비 **10% 역행** |
| → 거래당 최대 손실 | **equity의 1.0%** | 10% × 10% |
| → 레버리지 | **0.1x** | 사실상 무레버리지이므로 명목가 10% 손실 = 가격 10% 역행 |

### 검산 (사용자 예시 그대로)

```
equity                100.00 USDT
명목가                 10.00 USDT   (= 100 × 10%)
진입가                100,000 USDT/BTC  (예시)
수량                    0.0001 BTC   (= 10 / 100,000)
손절가                 90,000 USDT/BTC  (= 진입가 × 0.90)
손절 시 손실            1.00 USDT    (= 10 × 10%)
손절 후 equity         99.00 USDT   ✅ 사용자 명시와 일치
```

> ⚠ **위 예시(equity 100 USDT)는 실제로는 주문이 불가능하다.**
> Binance BTCUSDT 선물의 `minNotional` 은 100 USDT 이므로 명목가 10 USDT 는 거부된다.
> 실제 최소 자본 조건은 D3 참조.

### 확정 설정 (`config/base.yaml`)

```yaml
risk:
  sizing_mode: "fixed_notional_pct"   # 기존 "risk_based" 에서 변경
  position_notional_pct: 0.10         # 명목가 = equity * 10%
  stop_loss_pct: 0.10                 # 진입가 대비 -10%
  # -> 거래당 최대 손실 = equity * 1.0%

  max_leverage: 1.0                   # 0.1x 설계이나 상한은 1.0 으로 방어
  max_notional_multiple: 0.15         # 명목가 상한 (10% + 여유)

  daily_loss_limit_pct: 0.02          # 손절 2회분
  max_drawdown_pct: 0.10              # 계좌 누적 DD 10% -> HALTED
  consecutive_loss_limit: 4
  cooldown_after_losses_min: 240

  daily_trade_limit: 2                # D2 반영
  min_hold_minutes: 120               # D2 반영
  reentry_cooldown_minutes: 60        # D2 반영
  max_holding_hours: 48               # D2 신설
  same_direction_limit: 2

  liquidation_buffer_multiple: 2.0    # 0.1x 에서는 자동 통과
  max_unprotected_ms: 5000

  take_profit:
    enabled: true
    mode: "pct"                       # R배수 대신 % 기준 (손절폭이 고정이므로)
    take_profit_pct: 0.05             # 잠정값 — Phase 4 에서 결정
```

### 이 구조에서 반드시 알아야 할 3가지

#### (1) -10% 손절은 보유기간 2~24시간에서 거의 발동하지 않는다

BTC의 일반적인 일간 변동폭은 수 %다. 24시간 내 **10% 역행**은 연 수 회 수준의 사건이다.

→ **손절가는 실질적 리스크 관리 수단이 아니라 파국 방지용 backstop 이 된다.**
→ 실제 손실 통제는 다음 3가지가 담당한다:
   1. **청산 신호** (`long_score <= exit_threshold`) ← 주 탈출 경로
   2. **`max_holding_hours = 48`** ← 2차 탈출 경로
   3. **`daily_loss_limit` / `max_drawdown`** ← 최종 안전망

이것은 잘못된 설정이 아니라 **다른 종류의 리스크 관리**다. 다만 설계가 이에 맞춰져야 한다.
→ [05-risk-engine.md](05-risk-engine.md) §3 을 이 구조로 갱신했다.

#### (2) 고정 명목가는 변동성에 적응하지 않는다

같은 10% 명목가라도 고변동 국면에서는 저변동 국면보다 몇 배 더 큰 손익 변동을 만든다.
→ 변동성 게이트 **G5.1 / G5.2 가 유일한 변동성 적응 장치**가 되므로, 이 게이트의 중요도가 올라간다.
→ 향후 선택지: `vol_target` 오버레이 (명목가를 ATR 백분위로 ±50% 조정). Phase 9 이후 A/B 검증 대상.

#### (3) 0.1x 레버리지는 비용에도, 수익에도 똑같이 적용된다

**좋은 점** — 비용 부담이 매우 낮다:

| 항목 | 값 |
|---|---|
| 왕복 비용 (명목가 기준) | ~13 bps |
| 왕복 비용 (equity 기준) | 0.10 × 13bps = **1.3 bps** |
| 연 200거래 시 비용 | **equity의 0.26%** |
| funding (8시간당, rate 0.01% 가정) | equity의 0.001% — 무시 가능 |

[12-critique.md](12-critique.md) §7 에서 지적한 비용 문제가 이 설정에서는 **사실상 해소**된다.

**대가** — 수익도 같은 비율로 축소된다. 가정을 명시한 예시:

> 가정: 거래당 명목가 대비 순 기댓값 +0.125% (승률 45%, 평균 +1.5% / −1.0%), 연 200거래.
> **이 엣지는 아직 검증되지 않았으며, 존재한다는 근거가 없다. 순수한 예시다.**

| 명목가 비율 | 거래당 최대손실 | 연 기대수익 (위 가정 하) | 연 비용 |
|---|---|---|---|
| **10% (확정)** | equity 1.0% | **약 +2.2%** | 0.26% |
| 30% | equity 3.0% | 약 +6.7% | 0.78% |
| 100% (1x) | equity 10.0% | 약 +22.3% | 2.6% |

수익과 손실이 **같은 배수로** 움직인다. 10%는 "손실을 1/10로 줄이는" 선택이며 동시에
"수익도 1/10로 줄이는" 선택이다. 안전한 출발점으로는 타당하다.

**권고**: Phase 4 검증에서 엣지가 확인되면, Phase 9에서 명목가 비율을
단계적으로(10% → 15% → 20%) 올리는 것을 고려한다. 한 번에 올리지 않는다.
검증되지 않은 상태에서 비율을 올리는 것은 손실만 확대한다.

---

## D6. 멀티 에이전트 개발 체계 ✅ 확정

여러 AI 코딩 에이전트(Claude Opus, Codex 계열 등)가 번갈아 작업하는 것을 **전제**로 한다.
토큰/사용량 제약 때문에 에이전트를 바꿔야 할 때 작업이 끊기면 안 된다.

### 인계 체계

| 파일 | 역할 | 갱신 시점 |
|---|---|---|
| **`PROGRESS.md`** | 현재 Phase, 다음 작업, 미해결 항목, 작업 로그 | **매 작업 종료 시 필수** |
| **`AGENTS.md`** | 작업 규약, 설계 원칙 7가지, 금지 사항 | 규칙 변경 시 |
| `CLAUDE.md` | `AGENTS.md` 로의 포인터 (3줄) | 거의 없음 |
| `docs/phase0/13-decisions.md` | 확정값 (최우선) | 결정 추가 시 |
| `docs/STRATEGY-CATALOG.md` | 매매 개념 (사람용) | 지표/임계값 변경 시 |

**원칙: 규칙은 한 곳(`AGENTS.md`)에만 둔다.** 에이전트별 관례 파일이 필요하면 내용을 복사하지 않고
포인터만 만든다. 두 군데에 있으면 반드시 어긋난다.

**대화 없이 재개 가능해야 한다.** 새 에이전트가 위 5개 파일만 읽고 이어받을 수 있어야 한다.

### 시스템 내부 LLM 도 공급자 비종속 (Phase 11+)

`src/auto_trading/ai/` 는 `LLMProvider` 프로토콜 뒤로 숨긴다.

```python
class LLMProvider(Protocol):
    def analyze(self, prompt: str, schema: type[T]) -> T: ...
```

- 구현체를 config 로 교체 (`AnthropicProvider`, `OpenAIProvider`, ...)
- `model` + `prompt_version` 을 `news_analyses` 에 반드시 기록 — **모델이 바뀌면 그건 다른 전략이다**
- 어떤 공급자든 주문 권한 없음. Strategy → Risk Engine 을 반드시 경유

---

## D7. Phase 보고 규약 ✅ 확정

모든 에이전트는 **작업 응답의 시작과 끝에 현재 Phase 와 다음 작업을 명시**한다.

```
📍 현재: Phase 2 — 전략 코어 / features 모듈
🎯 이번 작업: features/normalize.py causal z-score 구현
   ...
✅ 완료: features/normalize.py + 단위테스트 12건
📍 현재 Phase: 2 (전략 코어) — 3/8 항목
➡️ 다음 작업: Phase 2 — features/trend.py (B1 4h EMA 추세)
⚠️ 미해결: 없음
📦 커밋 제안: P2: features/normalize.py 인과적 z-score 구현
```

| 규칙 | 내용 |
|---|---|
| Phase 번호 | `10-roadmap.md` 번호만 사용. 임의 번호 금지 |
| 다음 작업 | 어느 Phase 의 어느 항목인지 구체적으로. "다음 단계 진행" 금지 |
| Phase 이동 | 게이트 미통과 시 이동 금지. 이동하려면 사용자 승인 |
| 미해결 | 없으면 "없음" 이라고 명시 |
| 갱신 | 작업 종료 시 `PROGRESS.md` 반드시 갱신 |
| 커밋 | **에이전트가 실행하지 않는다.** 한글 제목만 제안 (→ D8) |

전문: [`AGENTS.md`](../../AGENTS.md) §1

---

## D8. 커밋 정책 ✅ 확정

**커밋과 푸시는 사용자가 직접 한다. 에이전트는 제목만 제안한다.**

이유: 커밋 메시지를 제대로 쓰려면 `git diff` 를 컨텍스트에 올려야 하고, 변경이 크면
그것만으로 수천 토큰이 든다. 사용자가 직접 커밋하면 이 비용이 사라진다.
**Claude · Codex 모두 동일 적용.**

| 항목 | 규칙 |
|---|---|
| 기본 동작 | 응답 끝에 `📦 커밋 제안:` 으로 제목 제시. 실행 금지 |
| 제시 형식 | 사용자는 **GitHub Desktop(GUI)** 사용 → 명령어가 아니라 **제목 + 체크할 파일 목록** |
| 여러 논리 단위 | 제안 제목을 순서대로 여러 개 나열 |
| 실행 조건 | 사용자가 "커밋해줘" 라고 **명시할 때만** |
| 푸시 | 언제나 사용자 요청 시에만 |
| 제목 언어 | **한글**, 50자 이내 |
| 접두사 | `P0`~`P9` (Phase 번호). Phase 무관 시 `docs:` / `chore:` |
| 기술 식별자 | 파일명·함수명·라이브러리명은 원문 유지 |
| 본문 | 한글. **왜** 바꿨는지 (무엇을 바꿨는지는 diff가 말해준다) |

전문: [`AGENTS.md`](../../AGENTS.md) §4

---

## D9. 포지션 사이징 — 손절 우선, 레버리지는 결과값 ✅ 확정 (D4 사이징 방식을 대체)

### 결정

레버리지를 먼저 고정하지 않는다. 다음 순서로 계산한다.

```
손절 위치 (전략이 결정)  →  허용 손실 (계좌 1%)  →  포지션 크기  →  필요 레버리지
```

| 파라미터 | 값 | 비고 |
|---|---|---|
| `risk_per_trade` | **0.01** | 거래당 최대 손실 = equity × 1% (**D4 와 동일, 변경 없음**) |
| `margin_allocation` | **0.10** | 증거금 **상한** (목표 아님 — §D9-2) |
| `max_leverage` | **10** | 설계상 천장. Phase 8 은 **3** 으로 시작, Phase 9 에서 3→5→10 |
| `min_stop_distance` | **0.01** | §D9-3 에서 **역산된** 값. 임의값 아님 |
| `liquidation_safety_buffer` | **2.0** | 청산거리 ≥ 2 × 손절거리 |
| `leverage_limit_action` | `REDUCE_POSITION` | 대안: `SKIP_TRADE` |
| `tight_stop_action` | `WIDEN` | 대안: `SKIP` |

명목가 공식 (비용 포함 닫힌 해):

```
N = L / [ s·(1 + c_out) + c_in + c_out + f ]

L = equity × risk_per_trade     s = stop_distance_pct
c_in  = taker_fee + slippage_entry
c_out = taker_fee + slippage_stop        (스톱은 슬리피지 배수 적용)
f     = 예상 funding (보유기간 기준)
```

거래소 레버리지 (정수):

```
exchange_leverage = max(1, ceil(N / (equity × margin_allocation)))
margin_allocated  = N / exchange_leverage        # margin 상한 이하 보장
```

전체 설계: [05-risk-engine.md §3](05-risk-engine.md)

### D4 와의 관계 — 폐기가 아니라 일반화

D4 는 `명목가 = equity × 10%`, `손절 = 가격 −10%` 였다.
D9 공식에 **손절폭 10% 를 넣으면 명목가 98.4 (≈ equity 10%), 레버리지 1x** 가 나온다.
**즉 D4 는 D9 의 특수해다.** 거래당 위험 1% 도 동일하다.

| | D4 (구) | D9 (신) |
|---|---|---|
| 거래당 최대 손실 | equity 1% | equity 1% (**동일**) |
| 손절 위치 | 가격 −10% 고정 | **전략이 결정** (스윙/ATR/피보나치 등) |
| 명목가 | equity 10% 고정 | 손절폭에서 역산 (최대 equity 50%) |
| 레버리지 | 0.1x | 1x ~ 5x (결과값) |
| 익절 | +5% 고정 | **2R** (손절폭 가변이므로) |
| 손절의 역할 | 파국 방지 backstop (거의 미발동) | **실제 리스크 통제 수단** |

### D9 채택으로 바뀌는 것 3가지

#### 0. `min_stop_distance` 는 청산 시점을 늦추지 않는다 (오해 방지)

`min_stop_distance = 1%` 는 **"1% 손실이 날 때까지 버틴다"가 아니다.**
포지션 크기를 계산하는 **분모**일 뿐이다.

| 탈출 경로 | 발동 조건 | 손실 크기 |
|---|---|---|
| **신호 청산** (`long_score <= 45`) | 봉 마감마다 평가 | **무관.** 0.01% 든 0% 든 즉시 청산 |
| 최대 보유시간 (48h) | 시간 초과 | 무관 |
| 손절 주문 | 가격이 손절가 도달 | ≈ 예산 1% (backstop) |

**주 탈출 경로는 여전히 신호 청산이다.** 손절은 프로세스가 죽어도 작동하는 최후 방어선이다.
`min_stop_distance` 를 낮추면 "더 늦게 자른다"가 아니라 **"포지션이 커진다"** 가 된다.

#### 1. 손절이 실제로 작동하기 시작한다

D4 의 −10% 손절은 2~24시간 보유에서 거의 발동하지 않아, 실질 손실 통제를 청산 신호와
`max_holding_hours` 가 담당했다. D9 에서는 손절폭이 2~5% 수준이 되므로 **손절이 주 탈출 경로**가 된다.

→ `max_holding_hours = 48` 은 유지한다 (3차 방어선).
→ 백테스트에서 `close_reason` 분포가 D4 가정과 크게 달라진다. Phase 4 에서 재확인 필요.

#### 2. 명목가가 커져 슬리피지 꼬리위험이 확대된다

```
스톱을 5% 지나쳐 체결된 경우의 추가 손실
  D4          (명목가  98 = equity 10%):   4.9 USDT  (equity 0.5%)
  D9 / 2% 손절 (명목가 465 = equity 46%):  23.3 USDT  (equity 2.3%)  ← 4.7배
  D9 / 1% 손절 (명목가 869 = equity 87%):  43.5 USDT  (equity 4.4%)  ← 8.9배
```

"1% 손실"은 **목표이지 보장이 아니다.** `STOP_MARKET` 은 시장가 체결이다.
→ 그래서 Phase 8 은 `max_leverage = 3` 으로 시작하고, 실측 슬리피지 확인 후
Phase 9 에서 단계적으로 3 → 5 → 10 으로 올린다. **10 은 설계상 천장이지 출발값이 아니다.**

#### 3. R 배수가 제대로 정의된다

손절폭이 전략적으로 결정되므로 `planned_risk_usdt = estimated_loss_at_stop` 이 의미를 갖는다.
→ D4 에서 `realized_r_stop` / `realized_r_atr` 로 나눠 기록하기로 한 것은 **철회**한다.
→ `trades.realized_r` 하나로 충분하며, 이것이 사이징과 무관하게 전략을 평가하는 유일한 척도다.

### 설계 검토에서 정정한 사항 4가지

원안(GPT 논의안)에서 수학적·실무적으로 바로잡은 부분이다.

| # | 원안 | 정정 | 이유 |
|---|---|---|---|
| 1 | `MARGIN_ALLOCATION` = 한 거래에 쓰는 증거금 비율 | **증거금 상한**으로 재정의 | 증거금은 손실 크기를 결정하지 않는다. 청산가 거리만 결정한다 |
| 2 | `Position Notional = Max Loss / Stop Distance` | `N = L / [s(1+c_out) + c_in + c_out + f]` | 청산 수수료는 *청산 시점* 명목가에 부과된다. 단순식은 예산을 7.5% 초과한다 |
| 3 | `Required Leverage = N / Margin` 후 상한 검증 | `exchange_leverage = ceil(N / margin_cap)` | 거래소 레버리지는 정수다. 내림하면 증거금 상한을 넘는다 |
| 4 | `MIN_STOP_DISTANCE = 0.005` (예시) | **0.01** + 설정 정합성 검증 | 0.5% 손절은 비용이 위험예산의 23%. 또한 §D9-3 참조 |

### D9-2. 증거금은 손실을 제한하지 않는다 (개념 정정)

| 개념 | 무엇을 결정하나 |
|---|---|
| `position_notional` | **손실과 비용**. 손절 시 손실 = 명목가 × 손절폭 |
| `margin` | **청산가까지의 거리**. 손실 크기와 **무관** |
| `leverage` | 위 둘의 비율. 입력이 아니라 **결과** |

증거금이 많을수록(레버리지가 낮을수록) 청산가가 멀어져 안전하다.
따라서 `margin_allocation` 은 "목표"가 아니라 "상한"이며, **상한 안에서 최대한 많이** 넣는다.

### D9-3. ⚠ 세 파라미터는 독립적이지 않다

```
명목가 상한 = margin_allocation × max_leverage × equity = equity의 100%

레버리지 상한에 걸리기 시작하는 손절폭:
  s* = risk_per_trade / (margin_allocation × max_leverage) − (c_in + c_out)
     = 0.01 / 1.00 − 0.0015 = 0.85%
```

**손절폭이 0.85% 보다 좁으면 레버리지 캡에 무조건 걸린다.**
그러면 `REDUCE_POSITION` 이 상시 발동해 체계적으로 예산보다 적게 위험을 지고,
백테스트의 R 분포가 왜곡되어 전략 평가가 어려워진다.

→ **`min_stop_distance = 1%` 는 여기서 역산된 값이다** (0.85% 바로 위).

| 원하는 최소 손절폭 | 필요한 `max_leverage` (margin 10% 기준) |
|---|---|
| 3% | 3.3 이상 |
| 2% | 5 이상 |
| **1% (확정)** | **10 이상** |
| 0.5% | 20 이상 |

`config/schema.py` 가 기동 시 이 관계를 검증하고, 불일치하면 **기동을 중단**한다.
값 범위 검증만으로는 잡히지 않는 종류의 설정 사고이기 때문이다.

### 손절폭별 사이징 (equity 1,000 / risk 1%)

| 손절폭 | 명목가 | 계좌대비 | 레버리지 | 왕복비용 | 비용/예산 | 청산/손절 | 캡 |
|---|---|---|---|---|---|---|---|
| 0.5% | 1,537 | 154% | 16x | 2.30 | **23.0%** | 11.8x | **걸림** |
| **1.0%** (하한) | **869** | **87%** | **9x** | **1.30** | **13.0%** | 10.7x | — |
| 1.5% | 606 | 61% | 7x | 0.90 | 9.0% | 9.3x | — |
| 2.0% | 465 | 46% | 5x | 0.69 | 6.9% | 9.8x | — |
| 3.0% | 317 | 32% | 4x | 0.47 | 4.7% | 8.2x | — |
| 5.0% | 194 | 19% | 2x | 0.28 | 2.8% | 9.9x | — |
| 10.0% | 98 | 10% | 1x | 0.14 | 1.4% | 10.0x | — (= D4) |

**좁은 손절은 비싸다.** 같은 1% 위험을 지면서 비용은 2%대비 두 배, 명목가도 두 배다.

### 구현 범위 (Phase 2)

- `risk/sizing.py` — `PositionSizingEngine` (순수 함수)
- `risk/gates.py` — G4.11 `INVALID_STOP`, G4.12 `STOP_TOO_TIGHT` 추가
- `core/types.py` — `SizingPlan` 불변 타입 (13-decisions D9 §기록 항목)
- `config/schema.py` — 파라미터 정합성 검증
- `storage/` — `decisions.sizing_json` 컬럼 추가
- 단위 테스트: 닫힌 해 검산, 캡 동작, 청산 안전성, 반올림, 경계값


---

## D10. 운영 UI 및 배포 형태 ✅ 확정

전체 설계: [14-ui-and-packaging.md](14-ui-and-packaging.md)

### 결정 요약

| 항목 | 결정 |
|---|---|
| 대시보드 | **로컬 웹 UI, 읽기 전용, 별도 프로세스** |
| 스택 | FastAPI + Jinja2 + Chart.js (로컬 번들). **npm 빌드 단계 없음** |
| 접근 | `127.0.0.1:8787` 만 바인딩. 인증 없음 |
| 화면 | 요약 / 자산곡선 / **달력(일별 손익)** / 거래목록 / **거래상세(판단근거)** / 진단 |
| exe | **PyInstaller.** 대시보드는 권장, 매매 엔진은 백테스트·Paper 로 제한 |
| 구현 시점 | 스키마는 **Phase 1**, UI 는 **Phase 6**, exe 는 Phase 6 말 |

### 협상 불가 제약 4가지

| # | 제약 | 이유 |
|---|---|---|
| 1 | **UI 는 별도 프로세스** | UI 가 죽어도 매매는 계속된다. UI 버그가 자금에 닿지 않는다 |
| 2 | **DB 를 읽기 전용으로 연다** (`mode=ro`, `query_only=ON`) | 쓰기가 물리적으로 불가능해야 한다 |
| 3 | **쓰기 엔드포인트 0개** | 주문·설정 변경 API 를 만들지 않는다. 킬 스위치도 UI 에 넣지 않는다 |
| 4 | **거래소 API 키를 UI 프로세스에 로드하지 않음** | 대시보드는 자격증명을 아예 모른다 |

테스트로 강제한다 (14 §7): 대시보드 연결로 `INSERT` 시도 → 예외 발생,
FastAPI 라우트 중 `POST/PUT/DELETE/PATCH` 개수 = 0.

### Phase 1 에 지금 넣어야 하는 것 ⭐

UI 구현은 미뤄도 되지만 **기록하지 않은 데이터는 나중에 복구할 수 없다.**

- `account_snapshots` **1분 주기 기록** (자산 곡선 해상도)
- `v_daily_pnl` 뷰 (달력 화면 — 날짜별 실현손익)
- `v_daily_equity` 뷰 (미실현 포함 자산 변동)
- `schema_version` 테이블 (exe 와 DB 버전 불일치 감지)

### 달력 화면의 시간대

기본 **UTC**. Risk Engine 의 일일 손실 한도 리셋이 UTC 00:00 이므로 같은 기준을 써야
"오늘 왜 거래가 멈췄지"를 추적할 수 있다. KST 토글은 제공하되 화면에 시간대를 항상 표시한다.

### exe 에 대한 경고 (14 §4.2)

**실거래(Phase 8~9)는 Linux VPS + systemd 를 유지한다.**
Windows 개인 PC 는 절전·자동 업데이트 재부팅·회선 불안정 때문에 24시간 매매 호스트로 부적합하다.

거래소 `STOP_MARKET` 은 프로그램이 죽어도 작동하므로 파국은 막히지만,
**우리 전략의 주 탈출 경로는 신호 청산이다** (D9 §0).
프로세스가 멈추면 "손절은 되지만 익절·신호청산은 안 되는" 상태가 이어져 전략이 반쯤 무력화된다.

| 모드 | Windows exe |
|---|---|
| 백테스트 | ✅ |
| Paper Trading | ✅ |
| Testnet | ⚠️ 비권장 |
| **실거래** | ❌ **Linux VPS 사용** |

---

## D11. 검증할 시장 가설 ✅ 확정

### 가설 (한 문장)

> **BTC 무기한 선물은 4시간 추세가 명확한 구간에서, 15분 되돌림 이후 추세 방향으로
> 재개되는 경향이 있으며, 그 크기는 왕복 거래비용과 최소 요구 엣지를 초과한다.**

### 왜 가설을 먼저 쓰는가

가설이 없으면 **기각될 수 없다**. 데이터에 맞는 가중치는 언제나 존재하기 때문이다
([12-critique.md](12-critique.md) §1). "이 문장이 틀렸다"고 말할 수 있어야 검증이다.

이것은 **믿음이 아니라 검증 대상**이다. 기각되는 것이 정상적인 결과다.

### 가설이 지표 5개로 번역되는 방식

| 가설의 구절 | 담당 요소 |
|---|---|
| "4시간 추세가 명확한 구간에서" | **B1** 4h EMA(50) — bias 게이트. 불명확하면 진입 금지 |
| (암묵 전제) 정상적인 변동성 | **B2** 1h ATR 백분위 — 극단 구간 차단 |
| "15분 되돌림 이후" | **T1** 15m RSI(14) 35~50 되돌림 구간 |
| "추세 방향으로 재개" | **T2** 5m 30분 모멘텀 — 되돌림 종료 확인 |
| (암묵 전제) 실제 참여 | **T3** 5m 거래량 비율 — 확인 계수 |
| "왕복 거래비용과 최소 요구 엣지를 초과" | 비용 게이트 (06 §5) |

**지표가 먼저 있고 가설을 나중에 붙인 것이 아니다.** 가설의 각 구절이 요소 하나에 대응한다.
대응하지 않는 요소는 넣지 않는다.

### 무엇이 이 가설을 기각하는가

[07-backtest.md](07-backtest.md) §8 게이트 4 전체가 기각 조건이다. 특히:

| 관측 | 의미 |
|---|---|
| TEST 구간에서 비용 반영 후 기댓값 ≤ 0 | **가설 기각** |
| 랜덤 기준선 95백분위 미만 | 신호와 잡음을 구분할 수 없음 → 기각 |
| 비용 2배에서 소멸 | 엣지가 비용 마진 안에 있음 → 실거래 불가 |
| 1봉 지연에서 부호 반전 | look-ahead 누출 → 결과 무효 |
| walk-forward 창 60% 미만에서 양수 | 특정 기간에만 작동 → 기각 |

### 기각되면 무엇을 하는가

가설을 바꿔서 다시 시도한다. **게이트를 낮추지 않는다.**

다음 가설 후보 (순서는 미정, Phase 4 실패 시 논의):

1. 추세 지속이 아니라 **평균 회귀** — 레인지 국면에서 극단 되돌림 반전
2. **Funding rate 극단값** 을 과열 신호로 사용 (현재는 게이트로만 씀)
3. 보유기간을 **수 일**로 늘려 비용 비중을 낮춤

[12-critique.md](12-critique.md) §14 Q7 의 중단 기준(D5)이 아직 미정이다.
잠정 기준: **가설 3개가 모두 게이트 4에서 실패하면 접근 자체를 재검토한다.**

### 기록 위치

- 이 문장은 `backtest_runs.notes` 에 run 마다 기록한다
- 가설이 바뀌면 **새 가설 번호**를 부여하고 이전 가설의 검증 결과를 보존한다
  (같은 데이터에 가설을 몇 번 시도했는지가 Deflated Sharpe 계산의 입력이다)

---

## D5. 실패 시 중단 기준 — 미정 ⏸

[12-critique.md](12-critique.md) §14 Q7 미답변.

제안 (합의 전까지 이 값을 잠정 적용):
> **가설 3개를 시도해 모두 Phase 4 게이트를 통과하지 못하면 접근 자체를 재검토한다.**

이 기준이 없으면 게이트를 통과할 때까지 게이트를 낮추게 된다. 가장 흔한 실패 경로다.

---

## 확정값 요약 (Phase 1 구현 기준)

```yaml
# 확정
symbol: BTCUSDT
exchange: binance_usdm
position_mode: one_way
margin_mode: isolated

strategy:
  feature_count: 5                  # D1
  weighting: equal                  # D1
  structure: bias_trigger_gating    # 06 §1
  evaluation_interval: 1m
  target_holding: "2h~24h"          # D2

risk:                               # D9 (D4 사이징 대체)
  sizing_mode: risk_based
  risk_per_trade: 0.01              # 거래당 최대 손실 = equity 1%
  margin_allocation: 0.10           # 증거금 "상한"
  max_leverage: 10                  # 설계 천장. Phase 8 은 3, Phase 9 에서 3->5->10
  min_stop_distance: 0.01           # 정합성에서 역산 (캡 발동선 0.85%)
  liquidation_safety_buffer: 2.0
  leverage_limit_action: REDUCE_POSITION
  tight_stop_action: WIDEN
  take_profit: {mode: r_multiple, r_multiple: 2.0}
  max_drawdown_pct: 0.10
  daily_loss_limit_pct: 0.02
  daily_trade_limit: 2
  max_holding_hours: 48

excluded_from_mvp:                  # D1
  - elliott_wave_counting
  - news_llm_pipeline
  - macro_surprise_scoring
  - weight_optimization
  - variable_position_sizing
  - trailing_stop
  - limit_order_entry
  - multi_symbol
  - multi_exchange

ui:                                 # D10
  dashboard: read_only_local_web    # FastAPI, 127.0.0.1:8787, 별도 프로세스
  write_endpoints: 0                # 협상 불가
  packaging: pyinstaller            # 대시보드 권장 / 매매엔진은 backtest·paper 한정
  live_trading_host: linux_systemd  # Windows exe 로 실거래 금지
  implement_at: {schema: phase1, ui: phase6, exe: phase6}

dev_process:                        # D6 / D7
  multi_agent: true                 # Claude Opus / Codex 계열 교대 작업
  handoff_files: [PROGRESS.md, AGENTS.md, docs/phase0/13-decisions.md, docs/STRATEGY-CATALOG.md]
  phase_reporting: mandatory        # 응답 시작/종료 시 Phase 명시
  commit_policy: propose_only       # D8 — 에이전트는 커밋 실행 안 함, 한글 제목만 제안
  llm_provider_agnostic: true       # src/auto_trading/ai/ 는 Protocol 뒤로

market_hypothesis:                  # D11 확정
  id: H1
  statement: >
    BTC 무기한 선물은 4시간 추세가 명확한 구간에서, 15분 되돌림 이후 추세 방향으로
    재개되는 경향이 있으며, 그 크기는 왕복 거래비용과 최소 요구 엣지를 초과한다.
  falsified_by: docs/phase0/07-backtest.md 게이트 4

pending:
  - capital_size                    # D3
  - abort_criteria                  # D5
```
