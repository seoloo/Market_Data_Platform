# AGENTS.md — AI 에이전트 작업 규약

> 이 파일은 **모든 AI 코딩 에이전트**(Claude Code, Codex, 그 외)가 이 저장소에서 작업할 때
> 따라야 하는 공통 규칙이다. 도구·모델과 무관하게 동일하게 적용된다.
>
> Claude Code 는 `CLAUDE.md` 를, Codex 계열은 이 파일을 먼저 읽는다.
> **`CLAUDE.md` 는 이 파일을 가리키는 포인터일 뿐이며, 실제 규칙은 전부 여기에 있다.**

---

## 0. 작업 시작 전 — 반드시 이 순서로

```
1. PROGRESS.md 를 읽는다            ← 지금 어디까지 됐는지
2. docs/phase0/13-decisions.md 를 읽는다  ← 확정된 설정값
3. docs/phase0/10-roadmap.md 에서 현재 Phase 의 게이트를 확인한다
4. 작업 시작
```

**PROGRESS.md 를 읽지 않고 코드를 쓰지 않는다.** 다른 에이전트가 어디까지 했는지 모르는 상태로
작업하면 중복 구현·충돌·설계 이탈이 발생한다.

---

## 1. ⭐ 모든 응답의 시작과 끝 — Phase 보고 (필수)

사용자 요청에 대해 **작업을 수행하는 모든 응답**은 다음 형식을 지킨다.

### 응답 시작 — 현재 위치 선언

```
📍 현재: Phase 2 — 전략 코어 / features 모듈
🎯 이번 작업: features/normalize.py 의 causal z-score 구현
```

### 응답 종료 — 다음 작업 + 커밋 제안

```
✅ 완료: features/normalize.py + 단위테스트 12건
📍 현재 Phase: 2 (전략 코어) — 진행률 3/8 항목
➡️ 다음 작업: Phase 2 — features/trend.py (B1 4h EMA 추세 지표)
⚠️ 미해결: 없음
📦 커밋 제안: P2: features/normalize.py 인과적 z-score 구현
```

### 규칙

| 항목 | 규칙 |
|---|---|
| Phase 번호 | 반드시 `docs/phase0/10-roadmap.md` 의 번호를 쓴다 (임의 번호 금지) |
| 다음 작업 | **어느 Phase의 어느 항목인지** 구체적으로. "다음 단계 진행" 같은 표현 금지 |
| Phase 이동 | 게이트를 통과하지 않고 다음 Phase로 넘어가지 않는다. 넘어가려면 **사용자 승인** 필요 |
| 미해결 | 남긴 TODO, 실패한 테스트, 확인 필요 사항을 반드시 적는다. 없으면 "없음" |
| 갱신 | 작업 종료 시 **`PROGRESS.md` 를 반드시 갱신**한다 |
| 커밋 제안 | **커밋을 직접 실행하지 않고 제목만 알려준다** (→ §4) |

> 이 규칙이 있는 이유: 여러 에이전트가 번갈아 작업하므로, 다음 에이전트가 컨텍스트 없이
> 이어받을 수 있어야 한다. 대화 내용은 사라지지만 `PROGRESS.md` 는 남는다.

---

## 2. 프로젝트 개요 (30초 요약)

**BTC/USDT 무기한 선물 자동매매 시스템.** Binance USD-M 기준, Python.

목적은 "가격 예측"이 아니라
**"규칙을 일관되게 지키고, 모든 판단을 기록하고, 통계적 우위가 실제로 있는지 검증하는 것"** 이다.

| 확정 사항 | 값 |
|---|---|
| 심볼 | BTCUSDT (One-way, Isolated) |
| 지표 | 5개 고정, 등가중 |
| 구조 | Bias(4h/1h) 게이트 × Trigger(15m/5m) 타이밍 |
| 보유기간 | 목표 2~24시간, 최대 제한 없음 (반대 점수 역전 시 신호 청산) |
| 거래당 최대 손실 | 총액의 1% |
| 사이징 | 전략 손절가에서 역산, 증거금 상한 10%, 레버리지 최대 10x |
| 손절 | 스윙/ATR 중 더 먼 쪽, 최소 1% |
| 하루 거래 | 최대 2회 |

전체 설계: [`docs/phase0/`](docs/phase0/) · 전략 개념: [`docs/STRATEGY-CATALOG.md`](docs/STRATEGY-CATALOG.md)

---

## 3. 절대 어기면 안 되는 설계 원칙 7가지

이 원칙들은 **논의 없이 바꾸지 않는다.** 바꿔야 한다고 판단되면 코드를 고치지 말고 사용자에게 묻는다.

### 3.1 Pure Core — 전략·리스크 코드에 I/O 금지

```python
def decide(snapshot: MarketSnapshot, portfolio: PortfolioState, cfg: Config) -> Decision
```

`core/`, `features/`, `strategy/`, `risk/` 안에서 **금지**:
- `datetime.now()`, `time.time()` → `snapshot.as_of_ms` 를 쓴다
- 네트워크, DB, 파일 I/O
- `random` (시드 없는 난수)
- `exchange`, `storage`, `marketdata`, `notify`, `ai` 모듈 import

이유: 백테스트와 실거래가 **같은 함수**를 호출해야 결과가 일치한다.
CI의 `import-linter` 가 이 규칙을 검사한다.

### 3.2 미래 데이터 접근 금지 (look-ahead)

- 정규화는 반드시 `causal_zscore(series, lookback)` — 전체 표본 통계 금지
- `MinMaxScaler`/`StandardScaler` 를 전체 데이터에 `fit` 하지 않는다
- 형성 중인 봉을 전략 판단에 쓰지 않는다
- 상위 TF 봉은 **마감된 후**에만 스냅샷에 포함
- ZigZag 스윙은 `confirmed_at_ms <= as_of_ms` 인 것만

### 3.3 거래소가 진실의 원천

로컬 DB와 거래소가 다르면 **항상 거래소를 채택**하고, 차이를 `risk_events` 에 기록한다.

### 3.4 보호주문은 거래소에 건다

진입 체결 즉시 `STOP_MARKET` + `closePosition=true` 등록.
로컬 손절 로직만 있으면 프로세스가 죽는 순간 무방비가 된다.
등록 실패 3회 → 포지션 즉시 시장가 청산.

### 3.5 멱등 주문

`clientOrderId` 는 결정론적으로 생성한다.
같은 봉에 대한 진입 의도는 재시작해도 **같은 id** 가 나와야 하고, 거래소가 중복을 거부한다.

### 3.6 Fail-closed / 비대칭

| 상황 | 동작 |
|---|---|
| 데이터가 stale·결측 | **진입 거부** ("중립 점수 0" 금지) |
| 주문 상태 불명(UNKNOWN) | **신규 진입 전면 금지**, 재주문 전 거래소 조회 |
| 시스템이 DEGRADED/HALTED | 진입 금지, **청산은 항상 허용** |

**Risk Engine 은 진입 거부권만 갖는다. 청산을 막을 수 있으면 안 된다.**

### 3.7 모든 판단을 기록

주문을 내든 안 내든 **매 평가마다** `decisions` 행을 남긴다.
`config_version_id`, `code_version_id`, `snapshot_digest` 를 항상 포함한다.

---

## 4. 코드 규약

| 항목 | 규칙 |
|---|---|
| Python | 3.11+ |
| 타입 | **모든 함수에 type hints.** `core/features/strategy/risk/execution` 은 `mypy --strict` 통과 |
| 금액·수량 | **`Decimal` 전용.** `float` 금지. DB에는 문자열로 저장 |
| 시각 | UTC epoch **milliseconds** (`int`). 변수명에 `_ms` 접미사 |
| 비율 | 변수명에 단위 명시: `_pct`(0~1), `_bps`, `_ms`, `_min` |
| 상수 | 매직넘버 금지. 전부 `config/` 로 |
| 예외 | `core/errors.py` 의 도메인 예외 사용. bare `except:` 금지 |
| 로깅 | 구조화 (JSON lines). `print()` 금지 |
| 포맷 | `ruff format` |
| 린트 | `ruff check` |
| 반올림 | 수량은 **항상 내림** (`floor_to_step`) |

### ⭐ 커밋은 에이전트가 실행하지 않는다 — 제목만 제안한다

**기본 정책: 커밋과 푸시는 사용자가 직접 한다.**

이유: 커밋 메시지를 제대로 쓰려면 `git diff` 를 컨텍스트에 올려야 하는데, 변경이 크면
그 자체가 수천 토큰이다. 사용자가 직접 커밋하면 이 비용이 통째로 사라진다.
**Claude · Codex 모두 동일하게 적용된다.**

| 상황 | 에이전트 동작 |
|---|---|
| 기본 (요청 없음) | 응답 끝에 **`📦 커밋 제안:` 으로 제목만 제시.** `git commit` 실행 금지 |
| 여러 논리 단위로 나뉘는 작업 | 제안 제목을 **순서대로 여러 개** 나열 |
| 사용자가 "커밋해줘" 라고 명시 | 그때만 실행 |
| 푸시 | **언제나 사용자 요청 시에만** |

### 커밋 제안 형식 — 사용자는 **GitHub Desktop(GUI)** 을 쓴다

명령어가 아니라 **"제목 + 체크할 파일 목록"** 으로 제시한다.
GUI 에서는 체크박스로 파일을 고르고 Summary 칸에 제목을 넣으므로, 명령어는 쓸모가 없다.

```
📦 커밋 제안

[1] P1: 프로젝트 스캐폴딩 및 CI 구성
    체크할 파일:
      pyproject.toml
      README.md
      .gitattributes
      .github/workflows/ci.yml

[2] P1: core 모듈 구현 및 Pure Core 경계 테스트 추가
    체크할 파일:
      src/ (24개 전체)
      tests/ (9개 전체)
      PROGRESS.md
```

규칙:

- 파일이 많으면 `src/ (N개 전체)` 처럼 **개수와 함께** 요약한다. 24개를 일일이 나열하지 않는다
- 커밋을 나눌 때는 **한 번에 해도 되는 대안 제목**도 함께 제시한다 (나누는 것은 편의이지 필수가 아니다)
- `.venv/` 나 `.env` 가 변경 목록에 보이면 **경고**한다. `.gitignore` 가 제대로 동작하지 않는다는 뜻이다
- 명령어를 원하는 사용자를 위해 필요하면 덧붙일 수 있으나, **기본은 파일 목록 형식**이다

### 커밋 메시지 — 제목은 **반드시 한글**

```
P<번호>: <무엇을 했는지 한글로>

<본문 — 한글. 왜 이렇게 했는지. 선택>
```

**예시**

```
P1: candles 테이블 마이그레이션 추가
P2: features/normalize.py 인과적 z-score 구현
P2: RSI 되돌림 지표(T1) 추가 및 단위테스트
P3: 백테스트 funding 정산 로직 구현
P3: 슬리피지 모델을 변동성 비례로 변경

골든 테스트 기대값이 바뀜. 기존 모델은 고변동 구간에서
슬리피지를 과소평가하여 백테스트가 낙관적으로 나왔음.
```

| 규칙 | 내용 |
|---|---|
| 제목 언어 | **한글** |
| 제목 길이 | 50자 이내 |
| 접두사 | `P0` ~ `P9` (Phase 번호). Phase 무관한 작업은 `docs:` / `chore:` |
| 기술 식별자 | 파일명·함수명·클래스명·라이브러리명은 **원문 유지** (`features/normalize.py`, `mypy`, `STOP_MARKET`) |
| 본문 | 한글. **왜** 바꿨는지. 무엇을 바꿨는지는 diff가 말해준다 |
| 골든 테스트 변경 | 결과가 바뀌면 **사유를 본문에 반드시 기록** (§5) |
| 서명 | 각 에이전트는 자기 도구의 관례대로 붙인다 (Claude Code 는 `Co-Authored-By` 라인) |

- 한 커밋 = 한 논리 단위 → 작업이 여러 단위면 **제안 제목도 여러 개**로 나눈다
- 테스트가 깨진 상태의 커밋을 제안하지 않는다
- **`.env`, API 키, 토큰이 포함될 수 있는 커밋은 제안조차 하지 않는다** (`.gitignore` 확인)

---

## 5. 테스트 규칙

| 새로 만드는 것 | 반드시 함께 |
|---|---|
| feature 함수 | 고정 입력 → 기대값 단위테스트 + **미래 데이터 무관성 테스트** |
| risk gate | 통과 / 경계 / 거부 3케이스 |
| 상태 전이 | property-based 테스트 (hypothesis) |
| 백테스트 로직 변경 | 골든 테스트 갱신 + **왜 결과가 바뀌었는지 커밋 메시지에 기록** |
| 거래소 연동 | FakeExchange 기반 통합 테스트 + 장애 주입 |

```bash
pytest                                   # 전체
pytest tests/leakage -v                  # look-ahead 탐지 (절대 스킵 금지)
mypy --strict src/auto_trading/core src/auto_trading/strategy src/auto_trading/risk
ruff check . && ruff format --check .
lint-imports                             # 의존 방향 검사
```

**테스트를 건너뛰고 "나중에 추가"하지 않는다.** 이 프로젝트에서 테스트는 기능이다.

---

## 6. 하면 안 되는 것

| 금지 | 이유 |
|---|---|
| 실거래 모드를 사용자 승인 없이 활성화 | 자금 손실 |
| 리스크 한도를 **느슨하게** 조정 | 13-decisions.md D4 확정값. 조이는 방향만 허용 |
| 백테스트 결과를 보고 파라미터 튜닝 후 "성과 개선" 주장 | 과최적화 |
| 지표를 임의로 추가 | STRATEGY-CATALOG.md §7 의 4조건 통과 필요 |
| 백테스트용 코드를 따로 작성 | Pure Core 원칙 위반 |
| Phase 게이트를 건너뛰기 | 검증 없는 진행 |
| "이 전략은 수익성이 있다"고 검증 없이 말하기 | 개발 원칙 §19-2 |
| Elliott 파동 카운팅 / 뉴스 LLM / 거시 surprise 를 MVP에 구현 | D1 확정 제외 사항 |
| 외부 라이브러리로 백테스트 (backtrader 등) | 라이브와 코드 경로가 갈라짐 |

---

## 7. 막혔을 때

| 상황 | 대응 |
|---|---|
| 설계가 애매함 | `docs/phase0/` 확인 → 없으면 **사용자에게 질문**. 추측해서 구현하지 않는다 |
| 확정값과 코드가 다름 | **13-decisions.md 가 우선**. 코드를 고친다 |
| 문서끼리 충돌 | 우선순위: `13-decisions.md` > `STRATEGY-CATALOG.md` > `docs/phase0/*` |
| 설계 원칙(§3)을 바꿔야 할 것 같음 | **고치지 말고 사용자에게 보고.** 이유와 대안을 함께 |
| 게이트 통과 실패 | 게이트를 낮추지 말고 보고. "실패"도 유효한 결과다 |

---

## 8. 모델·에이전트 이식성

이 저장소는 여러 AI 에이전트가 번갈아 작업하는 것을 전제로 한다.

| 보장하는 것 | 방법 |
|---|---|
| 컨텍스트 인계 | `PROGRESS.md` (매 작업 후 갱신 필수) |
| 설계 인계 | `docs/phase0/` + `13-decisions.md` |
| 전략 개념 인계 | `docs/STRATEGY-CATALOG.md` |
| 규칙 인계 | 이 파일 (`AGENTS.md`) |
| 대화 없이 재개 가능 | 위 4개만 읽으면 어디서든 이어받을 수 있어야 한다 |

### 에이전트별 진입 파일

| 에이전트 | 읽는 파일 |
|---|---|
| Claude Code (Opus 등) | `CLAUDE.md` → 이 파일로 연결 |
| Codex 계열 | `AGENTS.md` (이 파일) |
| 그 외 | `AGENTS.md` 를 직접 지정 |

**새 에이전트를 추가할 때**: 그 도구의 관례 파일명이 따로 있으면, 내용을 복사하지 말고
**이 파일을 가리키는 3줄짜리 포인터**를 만든다. 규칙이 두 군데에 있으면 반드시 어긋난다.

### 시스템 내부의 LLM 사용 (Phase 11 이후)

트레이딩 시스템 자체가 LLM을 호출하는 부분(`src/auto_trading/ai/`)도 **공급자 비종속**으로 만든다.

```python
class LLMProvider(Protocol):
    def analyze(self, prompt: str, schema: type[T]) -> T: ...
```

- 구현체: `AnthropicProvider`, `OpenAIProvider`, ... (config 로 선택)
- **`model` 과 `prompt_version` 을 `news_analyses` 테이블에 반드시 기록** — 모델이 바뀌면 그건 다른 전략이다
- `temperature=0` + 입력 해시 캐시로 결정론 확보
- 어떤 공급자든 **주문 권한은 없다.** 분석 결과는 Strategy → Risk Engine 을 반드시 거친다

---

## 9. 주요 파일 위치

```
PROGRESS.md                      ← 작업 시작 전 필독 / 작업 후 갱신 필수
AGENTS.md                        ← 이 파일
CLAUDE.md                        ← 포인터
docs/STRATEGY-CATALOG.md         ← 매매 개념 (사람용)
docs/phase0/
  README.md                      ← 설계 문서 색인
  13-decisions.md                ← ⭐ 확정값 (최우선)
  12-critique.md                 ← 설계 판단 근거
  10-roadmap.md                  ← Phase 정의 + 게이트
  05-risk-engine.md              ← 리스크 규칙
  06-scoring-engine.md           ← 점수 계산
config/base.yaml                 ← 실제 설정값
src/auto_trading/                ← 구현
tests/                           ← 테스트
```
