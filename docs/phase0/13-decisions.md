# 13 — Phase 0 확정 결정 기록

확정일: 2026-09-20
상태: **D1·D2·D4·D6·D7·D8 확정 / D3·D5 보류**

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

### Phase 8 착수 전에 확인해야 할 최소 자본 (D4 확정값 기준)

명목가 = equity × 10% 이므로, 거래소 제약을 만족하려면:

```
equity × 0.10  >=  max(min_notional, step_size × price)
```

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

## D4. 리스크 한도 — 고정 비율 명목가 방식 ✅ 확정

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

risk:
  sizing_mode: fixed_notional_pct   # D4
  position_notional_pct: 0.10
  stop_loss_pct: 0.10
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

dev_process:                        # D6 / D7
  multi_agent: true                 # Claude Opus / Codex 계열 교대 작업
  handoff_files: [PROGRESS.md, AGENTS.md, docs/phase0/13-decisions.md, docs/STRATEGY-CATALOG.md]
  phase_reporting: mandatory        # 응답 시작/종료 시 Phase 명시
  commit_policy: propose_only       # D8 — 에이전트는 커밋 실행 안 함, 한글 제목만 제안
  llm_provider_agnostic: true       # src/auto_trading/ai/ 는 Protocol 뒤로

pending:
  - market_hypothesis               # Phase 1 착수 블로킹
  - capital_size                    # D3
  - abort_criteria                  # D5
```
