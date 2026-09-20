# PROGRESS.md — 작업 현황

> **모든 AI 에이전트는 작업 시작 전 이 파일을 읽고, 작업 종료 시 반드시 갱신한다.**
> 대화 기록은 사라지지만 이 파일은 남는다. 여기 없는 것은 진행되지 않은 것이다.

**최종 갱신**: 2026-09-20
**갱신자**: Claude Opus 5
**저장소 상태**: b2794da (Phase 0 문서) + 미커밋 작업물 (Phase 1 스캐폴딩 + core)

---

## 📍 현재 위치

```
Phase 0 — 설계     ████████████████████  100%
Phase 1 — 기반     ███████░░░░░░░░░░░░░   35%  ← 여기
Phase 2 — 전략코어 ░░░░░░░░░░░░░░░░░░░░   0%
Phase 3 — 백테스트 ░░░░░░░░░░░░░░░░░░░░   0%
Phase 4 — 검증 ★   ░░░░░░░░░░░░░░░░░░░░   0%
Phase 5 — 거래소   ░░░░░░░░░░░░░░░░░░░░   0%
Phase 6 — Paper    ░░░░░░░░░░░░░░░░░░░░   0%
Phase 7 — Testnet  ░░░░░░░░░░░░░░░░░░░░   0%
Phase 8 — 소액실거래░░░░░░░░░░░░░░░░░░░░   0%
Phase 9 — 실거래   ░░░░░░░░░░░░░░░░░░░░   0%
```

Phase 정의: [`docs/phase0/10-roadmap.md`](docs/phase0/10-roadmap.md)

---

## ➡️ 다음 작업

**Phase 1 — 기반 구축 진행 중**

| 순서 | 작업 | 상태 |
|---|---|---|
| 1 | 프로젝트 스캐폴딩 (pyproject, ruff, mypy, pytest, pre-commit, CI, import-linter) | ✅ 완료 |
| 2 | `core/` — enums / errors / money / clock / hashing / types | ✅ 완료 |
| 3 | 아키텍처 테스트 (Pure Core 경계 AST 검사) | ✅ 완료 |
| 4 | **`config/` — pydantic 스키마 + yaml 로더 + 해시 + config_versions**<br>D9 파라미터 정합성 검증 포함 | ⬅️ **다음** |
| 5 | `storage/` — SQLite 스키마 + 마이그레이션 + repository | 대기 |
| 6 | `marketdata/backfill.py` — 과거 캔들/funding 수집 | 대기 |
| 7 | `marketdata/integrity.py` — 갭·중복·이상치 검증 | 대기 |
| 8 | (Phase 2) `risk/sizing.py` — PositionSizingEngine (D9) | 대기 |

---

## ✅ 완료된 작업

### Phase 0 — 설계 (2026-09-20)

| 산출물 | 내용 |
|---|---|
| `docs/phase0/README.md` | 색인, 종료 조건 |
| `docs/phase0/01-requirements.md` | 요구사항, 비목적, 성공기준 |
| `docs/phase0/02-architecture.md` | 아키텍처, 데이터흐름, 모듈구조 |
| `docs/phase0/03-data-model.md` | DB 스키마 DDL 초안 |
| `docs/phase0/04-state-machine.md` | 주문·포지션 상태머신, 복구 |
| `docs/phase0/05-risk-engine.md` | 리스크 게이트, 사이징 (확정값 반영) |
| `docs/phase0/06-scoring-engine.md` | Bias/Trigger 스코어링, 비용 게이트 |
| `docs/phase0/07-backtest.md` | 백테스트 엔진, 검증 프로토콜 |
| `docs/phase0/08-external-apis.md` | 외부 API 후보, 제약 |
| `docs/phase0/09-failure-modes.md` | 실패 지점 20종 |
| `docs/phase0/10-roadmap.md` | Phase 정의, 게이트, MVP 제외 |
| `docs/phase0/11-testing.md` | 테스트 전략 |
| `docs/phase0/12-critique.md` | 원 명세 비판적 검토 |
| `docs/phase0/13-decisions.md` | **확정 결정 기록** |
| `docs/STRATEGY-CATALOG.md` | **매매 개념 (사람용)** |
| `AGENTS.md` / `CLAUDE.md` / `PROGRESS.md` | 에이전트 작업 규약 |
| `.gitignore` | 시크릿 유출 방지 |

### Phase 1 — 기반 (2026-09-20, 미커밋)

| 산출물 | 내용 |
|---|---|
| `pyproject.toml` | 의존성, ruff, mypy strict, pytest, coverage, import-linter 계약 2건 |
| `README.md` | 프로젝트 개요, 설계 원칙 요약 |
| `.gitattributes` | LF 통일 (Windows 개발 / Linux 운영) |
| `.pre-commit-config.yaml` | ruff, gitleaks, 줄바꿈·대용량 파일 검사 |
| `.env.example` | 시크릿 템플릿 |
| `.github/workflows/ci.yml` | quality / test / secrets 3 job |
| `src/auto_trading/core/enums.py` | Timeframe(봉 경계 계산), OrderState, PositionState, VetoReason 등 13종 |
| `src/auto_trading/core/errors.py` | 도메인 예외 11종 (CausalityError 포함) |
| `src/auto_trading/core/money.py` | Decimal 전용 연산. **float 입력을 런타임 거부** |
| `src/auto_trading/core/clock.py` | Clock 프로토콜 + SimClock (RealClock 은 ops 에) |
| `src/auto_trading/core/hashing.py` | 결정론적 정규화 JSON + SHA-256 |
| `src/auto_trading/core/types.py` | Candle, MarketSnapshot, Decision 등 불변 타입 13종 + 검증 |
| `src/auto_trading/ops/clock.py` | RealClock (core 밖에 두어 core 순수성 유지) |
| `tests/core/` | money / enums / clock / hashing / types 테스트 |
| `tests/architecture/test_pure_core.py` | AST 로 I/O·난수·시계 사용 검사 |

**검증 결과**: 159 passed · mypy strict 0 errors · ruff clean · import-linter 2 contracts kept · coverage 93%

---

## 🔒 확정 사항 (변경 시 사용자 승인 필요)

전체: [`docs/phase0/13-decisions.md`](docs/phase0/13-decisions.md)

| 항목 | 값 |
|---|---|
| 심볼 / 거래소 | BTCUSDT / Binance USD-M (One-way, Isolated) |
| 지표 | **5개** 고정, 등가중 (B1 4h추세, B2 1h변동성, T1 15m RSI, T2 5m모멘텀, T3 5m거래량) |
| 구조 | Bias 게이트 × Trigger 타이밍 (곱셈) |
| 보유기간 | 2~24시간 / 최대 48h 강제청산 |
| **거래당 최대 손실** | **총액의 1%** (D9 — 이것이 기준) |
| 사이징 | 손절 우선 역산 `N = L / [s(1+c_out)+c_in+c_out+f]` |
| 손절 위치 | 전략 결정 (스윙/ATR 중 더 먼 쪽), 최소 **2%** |
| 증거금 상한 | 총액의 **10%** |
| 레버리지 | 자동 계산, 최대 **5x** (Phase 8 은 3x) |
| 익절 | **2R** |
| 하루 거래 | 최대 **2회** |
| 일일 손실 한도 | 총액 2% |
| 누적 DD 한도 | −10% → HALTED |
| MVP 제외 | Elliott 카운팅, 뉴스 LLM, 거시 surprise, 가중치 최적화, 가변 사이징, trailing stop, 지정가 진입, 다중 심볼/거래소 |

---

## ⚠️ 미해결 / 확인 필요

| # | 항목 | 담당 | 블로킹 |
|---|---|---|---|
| 1 | 검증할 시장 가설 1문장 확정 (작업 가설 잠정 채택) | 사용자 | **Phase 2** (Phase 1 은 무관) |
| 2 | 운용 자본 규모 (D3) | 사용자 | Phase 8 착수 |
| 3 | 중단 기준 — 가설 몇 개 실패 시 재검토 (D5) | 사용자 | Phase 4 |
| 4 | `take_profit.r_multiple` 2.0 은 잠정값 | Phase 4에서 결정 | — |
| 6 | D9 도입으로 `close_reason` 분포가 D4 가정과 달라짐 | Phase 4에서 재확인 | — |
| 5 | 슬리피지 모델 계수 (D9 에서 사이징에 직접 영향) | Phase 8 실측으로 보정 | — |

---

## 📋 Phase 게이트 체크리스트

각 Phase 종료 조건. 전체: [`docs/phase0/10-roadmap.md`](docs/phase0/10-roadmap.md)

### Phase 0 — 설계
- [x] MVP 범위 확정 (D1)
- [x] 목표 보유기간 확정 (D2)
- [x] 리스크 한도 확정 (D4)
- [x] DB 스키마 1차 확정
- [x] 에이전트 작업 규약 수립
- [x] 커밋 정책 (D8)
- [ ] 검증할 시장 가설 1문장 (작업 가설 잠정 채택 — Phase 2 전까지 확정 필요)

### Phase 1 — 기반
- [x] `mypy --strict` / ruff / pytest 통과 (159 passed, 0 errors)
- [x] import-linter Pure Core 경계 계약 통과
- [x] 아키텍처 테스트가 실제 위반을 탐지함을 확인
- [ ] 2019-01~현재 전 TF 캔들 갭 없이 적재 (갭은 전부 설명 가능)
- [ ] 1m → 5m/15m/1h 재집계가 거래소 원본과 일치
- [ ] 설정 해시 재현성 테스트 통과

### Phase 2 — 전략 코어
- [ ] `decide()` 가 I/O 없이 동작 (import-linter 통과)
- [ ] 동일 입력 → 동일 Decision 해시 (1000회)
- [ ] 전 게이트에 통과/거부 테스트 존재
- [ ] feature 상관행렬 |corr| < 0.7

### Phase 3 — 백테스트
- [ ] 합성 데이터에서 체결·수수료·funding 이 수기 계산과 일치
- [ ] 강제청산 시뮬레이션 동작
- [ ] 동일 run_id 재실행 시 지표 완전 일치 (골든)
- [ ] 리포트 전 항목 생성

### Phase 4 — 검증 ★ 최대 관문
- [ ] TEST 구간 비용 반영 후 양의 기댓값
- [ ] 거래 수 n ≥ 100
- [ ] 랜덤 기준선 95백분위 초과
- [ ] Buy & Hold 대비 Sharpe 우위
- [ ] 비용 2배 테스트 통과
- [ ] 파라미터 ±20% 섭동 통과
- [ ] 1봉 지연 / 신호 셔플에서 성과 소멸 (누출 없음)
- [ ] Walk-forward 창 60% 이상 양의 수익
- [ ] 강제청산 0회

### Phase 5~9
→ [`docs/phase0/10-roadmap.md`](docs/phase0/10-roadmap.md) §2

---

## 📝 작업 로그

| 날짜 | 에이전트 | Phase | 작업 | 결과 |
|---|---|---|---|---|
| 2026-09-20 | Claude Opus 5 | 0 | Phase 0 설계 문서 14종 작성 | 완료 |
| 2026-09-20 | Claude Opus 5 | 0 | 원 명세 비판적 검토, 권고 5건 수용 | D1 확정 |
| 2026-09-20 | Claude Opus 5 | 0 | 보유기간·리스크 한도 확정, 05 문서 갱신 | D2/D4 확정 |
| 2026-09-20 | Claude Opus 5 | 0 | 전략 카탈로그 + 에이전트 규약 작성 | 완료 |
| 2026-09-20 | Claude Opus 5 | 0 | 커밋 메시지 규약을 한글 제목으로 확정 (AGENTS.md §4) | 완료 |
| 2026-09-20 | Claude Opus 5 | 0 | 커밋 정책 확정 — 에이전트는 실행 않고 제목만 제안 (D8) | 완료 |
| 2026-09-20 | Claude Opus 5 | 1 | 프로젝트 스캐폴딩 (pyproject/ruff/mypy/pytest/CI/pre-commit/import-linter) | 완료 |
| 2026-09-20 | Claude Opus 5 | 1 | core 모듈 6종 + 테스트 159건 (coverage 93%) | 완료 |
| 2026-09-20 | Claude Opus 5 | 1 | Pure Core AST 아키텍처 테스트. 위반 주입으로 탐지 동작 확인 | 완료 |
| 2026-09-20 | Claude Opus 5 | 1 | 커밋 제안 형식을 GitHub Desktop 기준(파일 목록)으로 변경 | 완료 |
| 2026-09-20 | Claude Opus 5 | 0 | 사이징 방식 재설계 검토 후 D9 확정 (D4 사이징 대체). 05/13/CATALOG 갱신 | D9 확정 |

> **작성 규칙**: 작업 종료 시 이 표에 한 줄 추가. `날짜 / 에이전트 / Phase / 무엇을 / 결과`.
> 실패한 작업도 기록한다 — 같은 실패를 반복하지 않기 위해서다.
