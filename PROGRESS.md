# PROGRESS.md — 작업 현황

> **모든 AI 에이전트는 작업 시작 전 이 파일을 읽고, 작업 종료 시 반드시 갱신한다.**
> 대화 기록은 사라지지만 이 파일은 남는다. 여기 없는 것은 진행되지 않은 것이다.

**최종 갱신**: 2026-09-22
**갱신자**: Codex
**저장소 상태**: Phase 4 중단점 — H1·H2·H3 기각, 방향성 가설 탐색 종료

---

## 📍 현재 위치 — H3 (교차 심볼) 선별 완료 · 기각

```
Phase 0 — 설계     ████████████████████  100%
Phase 1 — 기반     ████████████████████  100%  ✅ 게이트 1 통과
Phase 2 — 전략코어 ████████████████████  100%  ✅ 게이트 2 통과
Phase 3 — 백테스트 ████████████████████  100%  ✅ 게이트 3 통과
Phase 4 — 검증 ★   ████████████████░░░░   80%  ← 여기 (H1·H2·H3 기각, 중단 기준 도달)
Phase 5 — 거래소   ░░░░░░░░░░░░░░░░░░░░   0%
Phase 6 — Paper    ░░░░░░░░░░░░░░░░░░░░   0%
Phase 7 — Testnet  ░░░░░░░░░░░░░░░░░░░░   0%
Phase 8 — 소액실거래░░░░░░░░░░░░░░░░░░░░   0%
Phase 9 — 실거래   ░░░░░░░░░░░░░░░░░░░░   0%
```

### 세 번의 가설을 검증했고, 사전 등록한 중단 기준에 도달했다

| 가설 | 결과 |
|---|---|
| H1 (가격 파생 5종) | 기각 — 점수가 결과와 무관 ([15](docs/phase0/15-h1-postmortem.md)) |
| H2 (포지셔닝 5종) | 기각 — 18bps 통과 0개, 재현 실패 ([17](docs/phase0/17-h2-screen-result.md)·[18](docs/phase0/18-h2-validation-result.md)) |
| H3 (교차 심볼 5종) | 기각 — 검정력은 충분했지만 최대 보수 하한 1.59bps < 비용 22bps ([24](docs/phase0/24-h3-screen-result.md)) |
| 비용 절감 (post-only) | 닫힘 — 신호 시점 역선택 ([20](docs/phase0/20-signal-time-fills.md)) |

한때 종료를 결정했으나 ([21](docs/phase0/21-closure.md)) **같은 날 철회**했다.
재검토 결과 진짜 제약이 드러났기 때문이다:

```
연간 독립표본 190개 → 1,814개 필요 → 9.5년 → 판정 불가
                ↑
        BTC 한 종목만 봤기 때문
```

**D1 의 단일 심볼 제약은 복잡도를 줄이려는 결정이었지 통계적 근거가 없었다.**
그 결정 하나가 모든 가설을 증명 불가능하게 만들고 있었다 →
[22-multi-symbol-pivot.md](docs/phase0/22-multi-symbol-pivot.md)

---

## ➡️ 다음 작업

**Phase 4 — 중단점 · 사용자 방향 결정 전 추가 가설/검증 금지**

| 순서 | 작업 | 상태 |
|---|---|---|
| A | 다중 심볼 5분봉 수집 (16종, 2023~2026) | ✅ 완료 |
| B | 교차 심볼 롱숏 **잡음 바닥** 측정 (무작위 순위, 신호 없이) | ✅ 완료 |
| C | 필요 효과 크기 산출 → **판정 가능 확인** | ✅ 완료 |
| D | H3 사전 등록 ([23](docs/phase0/23-h3-preregistration.md)) | ✅ 완료 |
| E | 폐지 심볼 포함 **시점별 유니버스** 구성 (생존 편향 제거) | ✅ 완료 |
| F | 유니버스 5분봉 + metrics 수집 | ✅ 완료 (2,183만 + 1,524만건) |
| F2 | 유니버스 **funding 수집** — 점검표 누락분 | ✅ 완료 (272/272 · 229,631건) |
| F3 | 선별기 **데이터 전제조건 검사** (`MIN_COVERAGE` 90%) | ✅ 완료 |
| G | **H3 선별 실행** (TRAIN 2023~2024) | ✅ 완료 — 5개 전부 실패, H3 기각 |
| H | VALIDATION 2025 / TEST 2026 | ⛔ 미실행 — TRAIN 통과 신호 없음 |

사전 등록 [23](docs/phase0/23-h3-preregistration.md) §6에 따라 H4나 신호 조합을 만들지
않는다. 다음 행동은 사용자가 **방향성 접근 종료** 또는 **프로젝트 목표 변경** 중 하나를
명시적으로 결정한 뒤에만 정한다. Phase 5로 이동할 전략 우위가 없으므로 게이트 4는
통과하지 못했다.

**인계 후 판정 감사 (2026-09-22)**: 원자료 digest와 신호 5종×horizon 3종의 완전성을
검사한 재판정 결과도 H3 기각이다. `reports/phase4/h3_screen_audited.json`에 별도 저장했다.
누락 파일을 기각으로 오독하는 경로와 비용 실패에 구조 실패 사유까지 붙는 표시 오류를
수정했다. 원천 데이터 재실행은 하지 않았으며, 감사 범위와 한계는 [24 §7](docs/phase0/24-h3-screen-result.md)에 기록했다.
검증: 전체 1,049 tests 통과 · leakage 42건 통과 · strict mypy 30개 파일 오류 0 ·
ruff 및 전체 포맷 검사 통과 · import-linter 4/4 유지.

> **F2 는 원래 점검표에 없었다.** 선별 직전에 확인해 보니 `funding_rates` 에
> BTCUSDT 하나뿐이었다 — 단일 심볼 시절 그대로다. 그대로 돌렸으면 `X1_rel_funding`
> 이 표본 0으로 "실패" 하고 **없는 데이터가 H3 기각으로 읽혔을 것**이다.
> 재발 방지로 선별기가 시작 전 적재율을 재고 90% 미만이면 멈춘다 (F3).

### 유니버스 (2026-09-22)

| | |
|---|---|
| 역대 USD-M USDT 심볼 | **864개** (아카이브 전수 조회) |
| 2023~2026 구간 존재 | 852개 · **그 중 폐지 19개** |
| 후보 (6개월 이상) | 671개 |
| **월별 상위 30에 든 심볼** | **272개** |
| 44개월 내내 유지 | **6개**뿐 (BTC·ETH·DOGE·BNB·XRP·SOL) |
| 월평균 교체 | **9.9개** |

선정 규칙: 매월 초 **직전 30일** 거래대금 상위 30개 (일 2천만 달러 하한, causal).
**토큰화 주식·금속 제외** — XAU·XAG·NVDA·MSTR·INTC·CRCL·EWY. 2026 유니버스의 10%였다.
H3 의 메커니즘은 암호화폐 무기한 선물의 포지셔닝 해소인데 이들은 실물 기초자산이
가격을 지배한다. **신호를 돌리기 전에** 정한 구조적 기준이다.

> 처음에 고른 15개 심볼은 전부 "오늘 살아남은 대형주" 였다. 그대로 썼으면 망한 종목이
> 숏 바구니에서 통째로 빠져 성과가 부풀려졌을 것이다.

### 검정력 확보 확인 (2026-09-22)

| horizon | 단일심볼 SD | **롱숏 SD** | 축소 | 가용 표본 | 25bps 효과 필요(비용 22) |
|---|---:|---:|---:|---:|---:|
| 2h | 116.5 | **28.8** | 4.0배 | 16,296 | 370 ✅ |
| 6h | 199.6 | **53.6** | 3.7배 | 5,432 | 1,279 ✅ |

`H2: 1,814 필요 / 703 보유 → 불가` 에서 `H3: 1,279 필요 / 5,432 보유 → 가능` 으로 바뀌었다.

⚠ 이것은 **"효과가 있다" 는 증거가 아니다.** 무작위 순위로 잰 잡음 바닥이며,
25bps 이상 효과가 **존재한다면 잡아낼 수 있다**는 것만 보였다. 효과가 비용(22bps)보다
작으면 데이터가 아무리 많아도 소용없다.

## 작업 이력

| 순서 | 작업 | 상태 |
|---|---|---|
| 1 | 프로젝트 스캐폴딩 (pyproject, ruff, mypy, pytest, pre-commit, CI, import-linter) | ✅ 완료 |
| 2 | `core/` — enums / errors / money / clock / hashing / types | ✅ 완료 |
| 3 | 아키텍처 테스트 (Pure Core 경계 AST 검사) | ✅ 완료 |
| 4 | `config/` — pydantic 스키마 + yaml 로더 + 해시 + D9 정합성 검증 | ✅ 완료 |
| 5 | `storage/` — SQLite 스키마 + 마이그레이션 + repository (D10 뷰 포함) | ✅ 완료 |
| 6 | `marketdata/archive.py` + `backfill.py` — 벌크 아카이브 수집 | ✅ 완료 |
| 7 | `marketdata/integrity.py` + `resample.py` — 무결성 검증 | ✅ 완료 |
| 8 | 실데이터 백필 실행 + 게이트 1 판정 | ✅ 완료 |
| 9 | (Phase 2) `features/normalize.py` — causal z-score / percentile | ✅ 완료 |
| 10 | (Phase 2) `features/` 지표 5종 (B1/B2/T1/T2/T3) | ✅ 완료 |
| 11 | (Phase 2) `features/structure.py` — ZigZag 스윙 (repainting 차단) | ✅ 완료 |
| 12 | (Phase 2) `risk/sizing.py` PositionSizingEngine (D9) + `risk/gates.py` | ✅ 완료 |
| 13 | (Phase 2) `strategy/policy.py` — 진입 임계값 + D12 점수 역전 청산 | ✅ 완료 |
| 14 | (Phase 2) `strategy/` bias·trigger·scoring·cost + `decide()` | ✅ 완료 |
| 15 | (Phase 2) `marketdata/snapshot.py` causal 관문 + 실데이터 `decide()` 검증 | ✅ 완료 |
| 16 | (Phase 2) **D14** — T1 을 시간적 setup 으로, 국면계수를 사이징으로 이전 + 비교 백테스트 | ✅ 완료 |
| 17 | (Phase 2→3) **funding rate 수집** — 게이트 차단 요인 해소 | ✅ 완료 |
| 18 | (Phase 3) `feed.py` + `fills.py` + `portfolio.py` + `engine.py` | ✅ 완료 |
| 19 | (Phase 3) `metrics.py` + `regimes.py` + `report.py` | ✅ 완료 |
| 20 | (Phase 3) buy&hold·랜덤·단일 feature 기준선 | ✅ 완료 |
| 21 | **(Phase 4) TRAIN 구간 검증 + edge table 추정** | 다중 horizon 게이트·24h 비중첩 진단 완료 — H1 비용 기준 실패 |
| 22 | **(Phase 4→2) H1 실패 후 가설 변경 여부 결정** | 사용자 승인 대기 — 게이트 완화·VALIDATION 진행 금지 |
| 23 | (Phase 6) `ui/` 대시보드 + `AutoTradingDashboard.exe` (D10) | 대기 |

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
| `docs/phase0/14-ui-and-packaging.md` | 대시보드 · exe 배포 설계 (D10) |
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

| `src/auto_trading/config/schema.py` | pydantic 스키마. `extra=forbid` + 범위 + **파라미터 간 정합성**(D9-3) |
| `src/auto_trading/config/loader.py` | yaml 병합 + 환경변수 오버레이 + 해시 + **시크릿 스캔** |
| `config/base.yaml` + 4개 오버레이 | backtest / paper / testnet / live |
| `tests/config/` | 스키마·로더 테스트 91건 |
| `src/auto_trading/storage/sql/001_initial.sql` | 전 테이블 23종 + 집계 뷰 3종 (D10) |
| `src/auto_trading/storage/db.py` | WAL 연결 + **읽기 전용 연결**(`mode=ro`+`query_only`) + 트랜잭션 |
| `src/auto_trading/storage/migrations.py` | 마이그레이션 러너 + **체크섬 드리프트 감지** + 버전 검사 |
| `src/auto_trading/storage/repositories/candles.py` | 캔들 upsert/조회/**갭 탐지** + causal 조회 |
| `src/auto_trading/storage/repositories/meta.py` | config/code 버전 멱등 등록 (재현성 고정점) |
| `src/auto_trading/ui/__init__.py` | 대시보드 자리표시자 (Phase 6) |
| `tests/storage/` | DB·마이그레이션·repository 테스트 72건 |
| `src/auto_trading/marketdata/archive.py` | data.binance.vision 클라이언트. **체크섬 검증 필수**, CSV 파싱, 단위 오판 방어 |
| `src/auto_trading/marketdata/backfill.py` | 월/일 아카이브 계획·재개·멱등 적재, 부분 실패 허용 |
| `src/auto_trading/marketdata/resample.py` | 순수 재집계 (1m → 상위 TF). 대조 전용 |
| `src/auto_trading/marketdata/integrity.py` | 갭·스파이크·정지가격·거래량0 + **1m↔상위TF 대조** |
| `tests/marketdata/` | MockTransport 기반 테스트 97건 (네트워크 미사용, 통합 3건 포함) |
| `scripts/backfill.py` | 백필+검증 실행 스크립트 (dry-run / 재개 / 검증만 지원) |

### Phase 2 — 전략 코어 (2026-09-21, 진행 중)

| 산출물 | 내용 |
|---|---|
| `src/auto_trading/features/normalize.py` | causal z-score / 백분위 / squash. **미래 데이터 물리적 차단** |
| `tests/features/test_normalize.py` | 50건 — causality 속성 테스트 포함 |
| `src/auto_trading/features/indicators.py` | SMA/EMA/TR/ATR/RSI (Decimal, Wilder 평활) |
| `src/auto_trading/features/base.py` | Feature 프로토콜 + FeatureResult (측정만, 해석은 strategy) |
| `src/auto_trading/features/trend.py` | **B1** 4h EMA 추세 (ATR 단위 정규화) |
| `src/auto_trading/features/volatility.py` | **B2** 1h ATR/가격 1년 백분위 |
| `src/auto_trading/features/momentum.py` | **T1** 15m RSI, **T2** 5m 모멘텀 z-score |
| `src/auto_trading/features/volume.py` | **T3** 5m 거래량 확인 계수 (곱셈) |
| `src/auto_trading/features/structure.py` | ZigZag 스윙. **`confirmed_at_ms` 로 repainting 차단** |
| `tests/features/test_indicators.py` + `test_features.py` + `test_structure.py` | 164건 |
| `src/auto_trading/risk/sizing.py` | D9 비용 포함 닫힌 해, 스윙/ATR 손절 선택, 수량 내림·레버리지 올림·청산 안전성 |
| `src/auto_trading/risk/gates.py` | G1~G7 전체 veto 수집, 안전 방향 우회 |
| `src/auto_trading/core/types.py` | `SizingPlan` 불변 기록 타입 추가 |
| `tests/risk/` | 사이징·게이트 통과/경계/거부 및 장애 주입 80건 |
| `src/auto_trading/strategy/policy.py` | 진입 70점, 롱/숏 대칭 점수 역전 신호청산. 동점 유지·보유시간/손익 무관 |
| `tests/strategy/test_policy.py` | D12 경계·대칭·손익 독립·무제한 보유 테스트 10건 |
| `src/auto_trading/strategy/bias.py` + `trigger.py` + `scoring.py` | D13 단일 squash Bias×Trigger 점수 합성. 70점 진입선 도달 가능 |
| `src/auto_trading/strategy/cost.py` | 시장가 왕복 비용 + 불리한 funding, TRAIN 표본 30건 미만 edge fail-closed |
| `src/auto_trading/strategy/decision.py` | 순수 `decide()` 전체 경로. 진입/거부/점수역전 청산, 결정론적 주문 의도 |
| `tests/strategy/test_scoring.py` + `test_decision.py` | 단일 압축·대칭·비용·fail-closed·진입/청산·1000회 결정론 테스트 |
| `src/auto_trading/features/registry.py` | feature 조립 **단일 지점**. `decide()` 와 `SnapshotBuilder` 가 같은 정의를 쓴다 |
| `src/auto_trading/marketdata/snapshot.py` | 저장소 → `MarketSnapshot` 인과성 관문. 필요 봉 수를 warm-up 에서 **파생** |
| `tests/marketdata/test_snapshot.py` + `tests/features/test_registry.py` | 형성 중 봉 배제·stale·갭·결측 fail-closed·mark price 출처 30건 |
| `src/auto_trading/strategy/setup.py` | **D14-1** T1 을 시간적 setup 조건으로. 되돌림 → 무효화 → 신선도 |
| `src/auto_trading/strategy/{bias,trigger,scoring}.py` | **D14-2** 국면계수를 `risk_scale` 로 분리. 방향 점수에 곱하지 않는다 |
| `src/auto_trading/risk/sizing.py` | `SizingRequest.risk_scale` — 국면 축소가 들어오는 **유일한** 지점 |
| `tests/strategy/test_scoring.py` + `tests/risk/test_sizing.py` | setup 성립/무효/만료/대칭, 국면계수가 점수를 안 깎음 등 40건 |
| `src/auto_trading/marketdata/funding.py` + `storage/repositories/funding.py` | funding 아카이브 백필 · 인과적 조회 · 결측 탐지 · 보유구간 정산 집계 |
| `src/auto_trading/storage/sql/002_funding_interval.sql` | `interval_hours` 컬럼 + 조회 인덱스 |
| `scripts/backfill_funding.py` | CLI. resume·dry-run·결측 리포트 |
| `tests/marketdata/test_funding.py` | 파싱·계획·인과성·지터 허용·체크섬 실패 31건 |

### Phase 3 — 백테스트 엔진 (2026-09-21, 진행 중)

| 산출물 | 내용 |
|---|---|
| `src/auto_trading/backtest/feed.py` | 전 구간 인메모리 적재 + 인과적 rolling window. 형성 중 봉 물리적 배제 |
| `src/auto_trading/backtest/fills.py` | 다음 1분봉 체결, 거래량 0 미체결, 갭·슬리피지, 동일 봉 손절 우선 |
| `src/auto_trading/backtest/portfolio.py` | 수수료·funding·MAE/MFE·강제청산 회계 + 경로 의존 `RiskState` 추적 |
| `src/auto_trading/backtest/engine.py` | 라이브와 같은 `decide()`를 호출하는 이벤트 재생 루프. 5m 판단→1m 체결 |
| `core/types.py` + `features/{registry,volatility}.py` | snapshot digest·B2 캐시. `decide()` 92ms→10.5ms/평가 |
| `tests/backtest/` | 체결·비용·funding·청산·인과성·결정론·fail-closed·일일 한도 72건 |
| `src/auto_trading/backtest/metrics.py` | 원 명세 지표 + 비용비율·R·DSR·bootstrap CI·DD 지속·MAE/MFE 등 07 §4 전 지표 |
| `src/auto_trading/backtest/regimes.py` | 200일 EMA·ATR 백분위 causal 국면 라벨 + 진입시점 기준 국면별 성과 |
| `src/auto_trading/backtest/report.py` + `charts.py` | 외부 plotting 의존성 없이 명세의 리포트 13종 생성 |
| `tests/backtest/test_metrics_and_report.py` + `test_regimes.py` | 골든 digest·동일 run_id 바이트 일치·PNG·경로탈출·국면 causality 7건 |
| `src/auto_trading/backtest/baselines.py` | 수수료 반영 Buy & Hold, 실제 거래 template 보존 랜덤 1,000회, B1/T1/T2 단독 전략 |
| `tests/backtest/test_baselines.py` | 기준선 수기값·골든 분포·다음 봉 진입·미래 무관성·리포트 연동 7건 |

**실데이터 스모크 (2024-01-15~29, 진단용 edge 60bps)**:
4,033회 평가 · 진입 후보 157회 · 거래 2건 · 최종 9,984.74 USDT(-0.15%) · 노출 29.4% ·
손절 1 / 2R 익절 1 · `SAME_DIRECTION_LIMIT` 154회 / `REENTRY_COOLDOWN` 7회.
edge 60bps는 아직 TRAIN에서 추정한 값이 아니므로 **성과 근거가 아니라 엔진 경로 스모크**다.

**실데이터 검증 (2026-09-19 기준)**: b1_trend=+2.43(ATR배수) · b2_volregime=0.179(백분위)
· t1_pullback RSI=49.6 · t2_momentum z=+0.58 · t3_volume=0.60

**`decide()` 실데이터 스윕 (2024-01~03, 1시간 간격 2,184회 평가)** — Phase 2 종료 판정 근거:

| 항목 | 결과 |
|---|---|
| 진입 | **16건** (롱 11 / 숏 5) ≈ 월 5건. D13 수정으로 70점 도달 실증 |
| 손절 출처 | **swing 16건 / ATR 0건** — 미해결 #8 실측 확정 |
| 손절폭 · 레버리지 | 평균 **3.14%** · **4.1x** (상한 10x 대비 여유) |
| 국면 분포 | normal 941 · high 1,053 · extreme 190. **진입 16건 전부 normal** |
| Spearman 최대 \|r\| | **0.392** (t1_rsi↔t2_z) → 06 §2.3 기준 0.7 통과 |
| **T1 기여** | **16건 중 12건이 정확히 0.00**, 나머지 4건도 ±0.20 → 미해결 #11 |

**D13 vs D14 동일 데이터 비교** (1시간 간격, 손절/2R 익절만 반영 — D12 점수청산 미반영):

| 연도 | 기존 D13 | 수정 D14 | 비고 |
|---|---|---|---|
| 2023 | −2.91R (48건) | **+14.40R** (68건) | D14 우세 |
| 2024 | **+10.57R** (38건) | +4.16R (79건) | D13 우세 |
| 2025 (~9월) | **+4.79R** (29건) | −1.25R (43건) | D13 우세 |
| **합계** | +12.45R / 115건 | +17.31R / 190건 | **평균 R 차이가 표준오차 이내** |

연도마다 부호가 뒤집히고 t≈0.9 로 **우열 판정 불가**. 표본 부족 + 주 청산 경로
(D12 점수 역전) 미반영이 원인이다. Phase 3 엔진 전까지 파라미터를 동결한다 (미해결 #15).
`setup_lookback_bars` 민감도 6/12/24 도 단조성이 없어 **성적으로 고르지 않았다**.

**구조 변경은 실측으로 확인됨**: 고변동 국면 진입 0건 → 31건(2024). end-to-end
`decide()` 40일 구간에서 진입 23건 승인, 국면별 리스크 1.00%/0.50% 적용 확인.

**검증 결과**: 937 passed · leakage 39 passed · mypy strict 0 errors · ruff clean
· import-linter 4 contracts kept

**스윙 확정지연 실측 (1시간봉 1년)**: 임계 1%→평균 2.5h(최대 46h) · **2%→6.3h(70h)**
· 3%→13.6h(94h) · 5%→38.3h(219h). 최근 구조 라벨 `HH/HL` (b1_trend +2.43 과 일치)

**실서버 스모크 (2026-09-21)**: 2024-01 실데이터 1m/5m/1h 적재 → 체크섬 전건 검증,
**재집계 대조 9,216 + 768 버킷 전부 일치**, 갭·이상치 0건, 8초 / 11MB.
전체 추정: 약 414만 봉 / **0.8GB** / 20~40분.

### Phase 4 — 전략 검증 (2026-09-22, 중단 기준 도달)

| 산출물 | 내용 |
|---|---|
| `src/auto_trading/backtest/edge.py` | TRAIN 전용 다중 horizon edge 관측·버킷 집계. 비용 전 gross return, `n<30` 은 edge 0 fail-closed |
| `strategy.evaluate_scores()` | `decide()`와 edge 추정이 공유하는 순수 점수 경로. 주문·사이징·snapshot 해시를 생략해 재생 시간 단축 |
| `scripts/estimate_edge_table.py` | 92일 단위 병렬 재생, 연도/분할 경계 라벨 보존, 결정론적 JSON+digest 출력 |
| `tests/backtest/test_edge.py` | 방향 대칭, 버킷 경계, 표본 하한, lookup 분리, digest 결정론 등 8건 |
| `config/edge_table.yaml` | 2020~2022 TRAIN 표를 설정에 고정. 2/6/12/24h가 모두 가용할 때 최저 edge 사용 |
| `config/schema.py` + `config/loader.py` | edge table 내용·파생값·digest 검증, 자동 로드 및 config hash 포함 |
| `strategy/decision.py` | 런타임 수동값이 없으면 score×side×regime 다중 horizon table 직접 조회 |
| `scripts/run_backtest.py` | SQLite 실데이터 → 공용 `decide()` 이벤트 재생 → 13종 리포트 생성 CLI |
| `backtest.edge.diagnose_edge_observations()` | 최대 horizon과 같은 24h embargo로 중첩 신호를 제거하고 원본과 비교 |

**가용 TRAIN edge 추정 (2020-01-01~2022-12-31, 5m 판단)**:
209,761회 평가 · 진입 점수 신호 6,650개 · 2/6/12/24h 관측 26,591개 · 라벨 누락 9개 ·
68개 버킷(표본 30개 이상 48개) · 테이블 digest `43a9f73c46af` · 최종 config hash
`d34550f5a48a`.

| horizon | LONG gross edge | SHORT gross edge | 양수 비율 LONG / SHORT |
|---|---:|---:|---:|
| 2h | −1.97bps | −3.74bps | 45.8% / 45.1% |
| 6h | −12.89bps | −8.71bps | 43.5% / 45.6% |
| 12h | −9.96bps | −8.25bps | 44.1% / 46.2% |
| 24h | +8.18bps | −5.23bps | 49.4% / 46.5% |

이는 중첩된 진입 점수 관측의 **비용 차감 전 TRAIN 평균**이며 성과 증거가 아니다. 대부분의
전체 평균이 음수이고 일부 세부 버킷만 양수다. 단일 horizon 을 성적순으로 고르지 않았으며,
VALIDATION·봉인 TEST에는 아직 접근하지 않았다. 동일 1주 입력 재실행 JSON SHA-256 일치 확인.

**다중 horizon 정책**: 2h·6h·12h·24h 네 셀이 모두 `n>=30` 이어야 하며, 그중 가장 낮은
gross edge를 진입 기대값으로 쓴다. 완전 가용한 score×side×regime 조합은 12개였고,
최저 edge가 왕복비용 13bps + 안전마진 5bps = 18bps를 넘는 조합은 **0개**였다
(최고도 −1.59bps). 누락/표본 부족은 그대로 fail-closed 한다.

**TRAIN 실제 엔진 재생 (2020~2022)**:

| 연도 | 평가 | 진입 신호 | `INSUFFICIENT_EDGE` | 체결 |
|---|---:|---:|---:|---:|
| 2020 | 0 | 0 | 0 | 0 |
| 2021 | 104,641 | 3,529 | 3,529 | 0 |
| 2022 | 105,121 | 3,121 | 3,121 | 0 |
| **합계** | **209,762** | **6,650** | **6,650** | **0** |

2020은 가용 데이터가 2020-01부터라 B2의 365일 causal warm-up을 채우지 못해 평가 0회다.
연도 분할은 사전 계산으로 모든 진입이 차단됨을 확인한 뒤 병렬 실행했으므로 포지션 상태 단절이
없다. 경제지표 캘린더가 아직 없어 `EVENT_BLACKOUT`도 모든 신호에 함께 기록됐지만,
`INSUFFICIENT_EDGE`만으로도 전 신호가 독립적으로 차단된다. 이 결과는 수익성 0%가 아니라
**현재 H1이 보수적 비용 게이트를 통과하지 못해 거래 가능한 표본이 없다는 실패 판정**이다.
게이트를 낮추거나 VALIDATION/TEST를 열지 않는다. TRAIN 안에서 신호 중첩·국면별 실패
원인까지 진단한 뒤, 가설 자체를 바꿀지는 사용자 승인 대상으로 남긴다.

**왜 2020~2022만 사용했는가**: 검증 프로토콜을 만들 때부터 2019~2022는 TRAIN,
2023은 VALIDATION, 2024~현재는 최종 1회 확인용 봉인 TEST로 시간 순서대로 고정했다.
로컬 데이터는 2020-01부터라 2019 표본이 없고, B2의 365일 causal warm-up 때문에 2020은
실질적으로 warm-up 역할을 했다. 최신 데이터가 덜 중요해서 제외한 것이 아니라, 전략을
설계하면서 최신 결과를 보면 그 구간에 맞춘 과최적화가 되어 TEST 의미가 사라지기 때문이다.

⚠️ **TEST 봉인 상태 정정**: 이번 edge 추정·진단은 2023년 이후를 읽지 않았지만, 이전
Phase 2/3 작업에서 2024-01 엔진 스모크, 2024~2025 D13/D14 비교, 2026 단일 시점 점검을
이미 수행했다. 따라서 2024~현재를 완전히 미열람한 최종 TEST라고 주장할 수 없다. 이 구간은
향후 참고/검증 구간으로만 취급하고, 최종 out-of-sample 판정은 전략을 다시 동결한 이후의
**미래 구간을 사전에 새로 지정해 전향적으로 수집**해야 한다. 새 TEST 경계 확정은 사용자 승인
대상이며, 확정 전에는 최신 데이터로 가설을 고르지 않는다.

**24시간 비중첩 진단** (결과를 보기 전에 최대 horizon=288×5m로 고정): 시간순 첫 신호를
선택한 뒤 24시간 안의 후속 신호를 모두 제외했다. 6,650개 중 6,182개(92.96%)가 제거되어
독립 표본은 468개였다. `n>=30`인 완전 조합은 12개→5개로 줄었고, 18bps 통과 조합은
원본과 비중첩 모두 **0개**였다. 비중첩 최고 조합은 `LONG/high/70~80점`, 최소 표본 38개,
다중 horizon 최저 edge **+2.03bps**로 비용 기준보다 약 16bps 부족했다. 따라서 반복 신호가
표본 수를 크게 부풀린 것은 맞지만, 중복 제거만으로 H1 실패가 해소되지는 않는다.

비중첩 전체 평균은 LONG 2/6/12/24h = `+13.42/-7.98/-10.19/-1.80bps`, SHORT =
`-0.97/-6.10/-29.56/-24.98bps`. 진단 digest `6a5742d7f801`. 이 결과로 현재 H1은
**가설 자체의 경제적 edge가 부족**하다고 판정한다. 다음 단계는 사용자 승인 후 Phase 2로
돌아가 H1을 수정하거나, 이 접근을 종료하는 것이다. 이번 단계에서는 2023년 이후 데이터에
추가 접근하지 않는다.

---

## 🔒 확정 사항 (변경 시 사용자 승인 필요)

전체: [`docs/phase0/13-decisions.md`](docs/phase0/13-decisions.md)

| 항목 | 값 |
|---|---|
| **검증할 가설 (H1)** | 4h 추세 구간에서 15m 되돌림 후 추세 재개. 크기가 비용을 초과 (D11) |
| 심볼 / 거래소 | BTCUSDT / Binance USD-M (One-way, Isolated) |
| 지표 | **5개** 고정, 등가중 (B1 4h추세, B2 1h변동성, T1 15m RSI, T2 5m모멘텀, T3 5m거래량) |
| 구조 | Bias 게이트 × Trigger 타이밍 (곱셈) |
| 보유기간 / 청산 | 목표 2~24시간, 최대 제한 없음 / 반대 점수 엄격 역전 시 `SIGNAL` 청산 (D12) |
| **거래당 최대 손실** | **총액의 1%** (D9 — 이것이 기준) |
| 사이징 | 손절 우선 역산 `N = L / [s(1+c_out)+c_in+c_out+f]` |
| 손절 위치 | 전략 결정 (스윙/ATR 중 더 먼 쪽), 최소 **1%** |
| 증거금 상한 | 총액의 **10%** |
| 레버리지 | 자동 계산, 최대 **10x** (Phase 8 은 3x, Phase 9 에서 3→5→10) |
| 익절 | **2R** |
| 하루 거래 | 최대 **2회** |
| 일일 손실 한도 | 총액 2% |
| 누적 DD 한도 | −10% → HALTED |
| 결과 확인 UI | 로컬 대시보드 (읽기 전용, 별도 프로세스) — **Phase 6**. 스키마는 Phase 1 |
| 배포 | 대시보드 exe 권장 / **실거래는 Linux VPS + systemd** |
| MVP 제외 | Elliott 카운팅, 뉴스 LLM, 거시 surprise, 가중치 최적화, 가변 사이징, trailing stop, 지정가 진입, 다중 심볼/거래소 |

---

## 🔴 H1 기각 (2026-09-22)

가설 H1(D11)이 TRAIN 에서 기각됐다. 전문: [15-h1-postmortem.md](docs/phase0/15-h1-postmortem.md)
· 결정: [D15](docs/phase0/13-decisions.md) · 다음 제안: [16-h2-proposal.md](docs/phase0/16-h2-proposal.md)

**Codex 판정 (2026-09-22)**: 진입 신호 6,650건 전부 `INSUFFICIENT_EDGE`, 체결 0건.
24시간 비중첩 468건에서도 18bps 통과 조합 0개, 최고 +2.03bps.

**Claude 독립 검증 (2026-09-22)** — 결론 지지, 근거 보강:

| 확인 | 결과 |
|---|---|
| 게이트 무력화로 인한 오판인가 | **아니다.** T1 setup 성립률 70.2% — 실제로 구속력이 있었다 |
| 기준선이 공정한가 | **공정하다.** 2021(+59.9%)·2022(−64.2%) 상쇄로 무조건부 선행수익 ≈ 0.0bps |
| 점수의 예측력 | **없다.** 80~90점이 70~80점보다 나쁨. 양수 비율 44~54% |
| T2 모멘텀의 기여 | **음수.** 임계 1.0→2.0→3.0 에서 +0.01→−1.43→−5.46bps 단조 악화 |
| embargo 를 horizon 에 맞추면 | 정밀도 4배 개선 (2h ±4.6→±1.5bps). **그래도 통과 0개** |

**추가 발견 — 24시간 보유는 측정 불가**: 18bps 를 2σ 로 분해하려면 24h 는 독립 표본
1,939개(최소 5.3년)가 필요한데 2년에 730개뿐이다. 2h/6h/12h 는 측정 가능.
→ **D15-1 로 검증 horizon 상한을 6h 로 내렸다** (D2 수정, 사용자 승인 대상).

**중첩 표본의 함정 기록**: 중첩 포함 구간 분석에서 `z_aligned ≤ −3` → +16.65bps
(62.3% 양수)가 나왔으나, 중복 제거 후 −1.5bps 로 소멸. 이런 값으로 가설을 만드는 것이
곧 과최적화다.

---

## 🔴 선택지 B 종료 — 신호 시점 재측정이 결론을 뒤집었다 (2026-09-22)

전문: [20-signal-time-fills.md](docs/phase0/20-signal-time-fills.md)
· [19](docs/phase0/19-maker-cost-measurement.md) §3 판정 **철회**

19 가 스스로 가장 큰 한계로 지목했던 "무작위 시점에서 쟀다" 가 실제로 결론을 뒤집었다.
F5 하위 decile 421건에서 다시 쟀다.

| | 무작위 시점 (19) | **F5 신호 시점 (20)** |
|---|---:|---:|
| 체결률 (15분) | 97.7% | **84.1%** |
| 미체결분 역선택 | −23.23 ± 7.02 | **−91.39 ± 32.94** |
| **기대 역선택 비용** | **0.53bps** | **14.5bps** (27배) |
| taker 대비 | +3.97 유리 | **−15.9 불리** |

**대기 1·5·15·30·60분 전 구간에서 maker 가 taker 에 진다** (최선 60분 +13.59 vs +21.52).

**원인**: F5 하위 decile 은 "이후 상승" 신호다. 매수 지정가를 아래 걸면 **가격이 나를
두고 올라간다.** 신호가 맞을수록 체결이 안 된다 — F5 만의 문제가 아니라 **방향성 신호
전반의 성질**이다.

### 결과: 비용 18bps 유지 → F5 판정 불가로 복귀

| 기준 | 필요 표본 | 필요 연수 | 가용 4.7년 |
|---|---:|---:|---|
| 18bps (taker) | 1,814 | **9.5년** | ❌ |

**교훈**: 무작위 시점 체결 통계로 신호 전략의 비용을 가정하면 안 된다. 그 차이(14bps)가
전략 전체 기대값보다 크다. 19 의 숫자를 그대로 썼다면 실거래에서야 발견했을 것이다.

### 남은 선택지 (사용자 결정)

| | 내용 | 상태 |
|---|---|---|
| **A. 종료** | 두 가설 기각 + 비용 경로도 닫힘. 07 §8 이 말한 정상적 결과 | 유효 |
| ~~B. 비용 절감~~ | post-only 는 신호 시점에서 손해 | **닫힘** |
| **C. 목표 변경** | 방향성 포기, funding 수취·베이시스 등 구조적 수익 | 유효 (다른 프로젝트) |

16 §4.4 에 따라 **세 번째 방향성 가설을 만들지 않는다.** 결정 전 추가 데이터 탐색 없음.

---

## ⛔ (철회됨) 선택지 B 검증: post-only 비용 실측 — 무작위 시점 기준

전문: [19-maker-cost-measurement.md](docs/phase0/19-maker-cost-measurement.md)

18 §3.3 의 B("비용을 줄이면 판정 가능해진다")를 실측했다. 2021+2023, 17,520 시점 × 양방향.

| 대기 | 체결률 | 미체결분 역선택 | taker 대비 개선 |
|---|---:|---:|---:|
| 1분 | 91.1% | −10.09 ± 3.67 | +3.59 |
| **15분** | **97.7%** | −23.23 ± 7.02 (t=−3.31) | **+3.97** |
| 30분 | 98.5% | −39.51 ± 7.73 | +3.89 |

**역선택은 실재한다** — 체결된 시점 −0.53bps vs 놓친 시점 **+22.70bps**. 오래 기다릴수록
커진다. 하지만 체결률이 높아 **기대 비용은 0.5~0.9bps** 로 희석되고, 순개선은 ~4bps다.
미체결 시 **추격은 오히려 손해**(−9.18 vs 취소 −9.03).

### 판정: B 성립. 단 가정보다 작다

| | 18 §3.3 가정 | 실측 |
|---|---:|---:|
| 왕복 비용 | 7.5bps | **9.0bps** |
| 기준 (마진 포함) | 12.5bps | **14.0bps** |

청산은 보호주문이라 어차피 taker(6.5bps)라서 진입 쪽만 6.5→2.0으로 준다.

**그래도 결정적이다** — F5 판정에 필요한 데이터가 바뀐다:

| 기준 | 필요 표본 | 필요 연수 | 가용 4.7년 |
|---|---:|---:|---|
| 18bps (taker) | 1,814 | 9.5년 | ❌ |
| **14bps (maker 실측)** | **634** | **3.3년** | ✅ |

**F5 가 "판정 불가" 에서 "판정 가능" 으로 옮겨졌다.**

⚠ 이 측정은 **무작위 시점** 기준이다. F5 신호 시점은 시장이 빠른 구간일 수 있어
체결 양상이 다를 수 있다 → 다음 단계에서 F5 시점으로 한정해 재측정해야 한다 (19 §4).

---

## 🔴 H2 기각 — 접근 재검토 단계 (2026-09-22)

전문: [18-h2-validation-result.md](docs/phase0/18-h2-validation-result.md)

F5 단독을 정의 그대로 VALIDATION(2024)에서 1회 재현했다. 다른 네 신호는 보지 않았다.

| 사전 등록 기준 | 결과 | |
|---|---|---|
| 5분위 단조성 유지 | 6h `+11.0 +5.7 +9.1 +3.8 −6.2` | ❌ |
| 하위 decile 부호 동일(양수) | 2h +4.92 · 4h +9.76 · 6h +15.08 | ✅ |
| 6h 하한 > 0 | −2.79 | ❌ |

**H2 최종 기각** (16 §4.4). 세 번째 가설을 만들지 않는다.

**다만 "효과가 사라졌다"가 아니라 "구분할 힘이 없었다"이다.**
TRAIN +34.57±9.96(n=421) vs VALID +15.08±8.93(n=189) → 차이 +19.49±13.38, **t=1.46**.
두 결과는 서로 구별되지 않는다. 내가 1년짜리 재현 검정을 등록한 것이 설계 실수였다.

### 진짜 제약은 가설이 아니라 산수다

```
효과 크기 ~20bps  ·  왕복비용 18bps  ·  6h 수익률 SD 123bps  ·  연간 독립표본 190
```

| 확인하려는 것 | 필요 표본 | 필요 연수 |
|---|---:|---:|
| 0 보다 크다 | 107 | 0.6년 |
| **maker 비용 12.5bps 돌파** | **476** | **2.5년** ← 가능 |
| **taker 비용 18bps 돌파** | **1,814** | **9.5년** ← 데이터 없음 |

**taker 기준으로는 어떤 가설도 판정할 수 없다.** 쓸 수 있는 metrics 는 4.5년뿐이다.

### 선택지 (사용자 결정)

| | 내용 |
|---|---|
| **A. 종료** | 두 가설 기각. 미리 정한 대로 세 번째를 만들지 않는다 (07 §8: 정상적 결과) |
| **B. 비용을 먼저 줄인다** | post-only 로 기준 18→12.5bps. 필요 표본 1,814→476 으로 **판정 가능해진다**. 단, 먼저 할 일은 **미체결률 모델링**이지 재검정이 아니다 |
| **C. 목표 변경** | 방향성 예측 포기, funding 수취·베이시스 등 구조적 수익. 사실상 다른 프로젝트 |

---

## 🟡 H2 선별 결과 (2026-09-22)

전문: [17-h2-screen-result.md](docs/phase0/17-h2-screen-result.md)
· 제안: [16-h2-proposal.md](docs/phase0/16-h2-proposal.md)

**사전 등록 기준(18bps, n≥200)으로 5개 신호 전부 실패.** 기준을 낮추지 않았고
조합 탐색으로 넘어가지 않았다 (16 §4.4).

| 신호 | 최고 하한 | 판정 |
|---|---:|---|
| F1_funding_extreme | −4.24 | ❌ 연도 불일치, 구조 없음 |
| F2_oi_surge | +1.46 | ❌ |
| F3_oi_price_divergence | +0.12 | ❌ |
| F4_taker_imbalance | +0.30 | ❌ |
| **F5_crowd_divergence** | **+14.66** | ❌ 하지만 **구조가 있다** (아래) |

**F5 는 나머지와 질적으로 다르다** — 5분위 × 3 horizon **15칸 전부 단조**,
2021·2023 **연도 일관**, horizon 이 길수록 효과 증가:

| horizon | Q1 | Q2 | Q3 | Q4 | Q5 | 하위 decile |
|---|---:|---:|---:|---:|---:|---|
| 2h | +8.9 | +5.3 | +3.9 | −0.4 | −4.8 | +11.63±3.68 (53.3% 양수) |
| 4h | +15.9 | +10.4 | +5.3 | −1.1 | −8.7 | +22.74±7.20 |
| 6h | +20.9 | +12.6 | +4.7 | −2.6 | −9.3 | **+34.57±9.96** |

`F5 = z(전체 계좌 롱숏비) − z(상위 트레이더 롱숏비)`. 낮을수록(군중이 덜 롱) 이후 상승.
**H2 의 머리말 메커니즘이었던 funding(F1)은 아무것도 내지 못했다** — 가설 문장의
절반("쏠림 비용")은 틀렸고 다른 절반("누가 쏠렸나")만 남았다.

**TRAIN 성적으로 통과시키지 않는다.** 5개 중 가장 잘 나온 하나라 다중 비교로 부풀려져
있다. 제안: **F5 단독을 정의 그대로 VALIDATION(2024)에서 1회 재현** (17 §4, 승인 필요).

**데이터 품질 발견**: 2021 아카이브는 **전 행이 중복**(577행), 빈 값 행과 하루 전체가
빈 날이 존재, **2022 는 아카이브가 사실상 비어 있음**(12.7%)이라 TRAIN 을 2021+2023 으로 정정.

---

## ⚠️ 미해결 / 확인 필요

| # | 항목 | 담당 | 블로킹 |
|---|---|---|---|
| ~~1~~ | ~~검증할 시장 가설~~ → **D11 확정 (가설 H1)** | — | 해소됨 |
| 2 | 운용 자본 규모 (D3) | 사용자 | Phase 8 착수 |
| 3 | 중단 기준 — 가설 몇 개 실패 시 재검토 (D5) | 사용자 | Phase 4 |
| 4 | `take_profit.r_multiple` 2.0 은 잠정값 | Phase 4에서 결정 | — |
| ~~7~~ | ~~이중 squash로 진입 임계값 70 도달 불가~~ → **D13 단일 squash 확정** | — | 해소됨 |
| 8 | **스윙 확정지연(평균 6.3h)으로 구조적 손절이 사실상 항상 채택됨** — 실데이터 16/16건<br>실측 손절폭 평균 3.14%, 레버리지 4.1x, `min_stop_distance` 1% 는 미발동 | Phase 4에서 의도 확인 | — |
| ~~11~~ | ~~T1 이 진입에 기여하지 않음~~ → **D14-1 로 setup 조건화** | — | 해소됨 |
| ~~12~~ | ~~고변동 국면에서 진입 불가~~ → **D14-2 로 국면계수를 사이징으로 이전** | — | 해소됨 |
| 15 | **D14 의 성과 우열은 아직 미확인** — 근사 비교에서 연도별로 부호가 뒤집힌다.<br>D12 점수 역전 청산(주 청산 경로)을 반영하지 못한 근사이므로 판단 불가.<br>**Phase 3 엔진으로 재측정하기 전까지 임계값·파라미터를 만지지 않는다** | Phase 3 | Phase 4 |
| 16 | `setup_lookback_bars=12` 는 선험적 기본값. 민감도 6/12/24 에서 단조성 없음 | Phase 4 | — |
| ~~13~~ | ~~`funding_rates` 비어 있음~~ → **7,305건 적재, 결측 0건. end-to-end 진입 승인 확인** | — | 해소됨 |
| ~~14~~ | ~~`decide()` 1회 92ms~~ → digest/B2 캐시로 **10.5ms/평가** 실측 | — | 해소됨 |
| 17 | **동일 방향 연속 진입 제한이 실데이터 후보 대부분을 차단** — 2주 스모크에서 157개 후보 중 154개가 `SAME_DIRECTION_LIMIT`.<br>확정 리스크 한도이므로 임의 완화 금지. Phase 3 전체 재생 통계로 재확인 | Phase 3 | Phase 4 |
| 18 | **계획 TRAIN은 2019~2022지만 로컬 데이터는 2020-01-01부터 시작** — 현재 edge table은 2020~2022 가용 TRAIN 기준 | 사용자/Phase 4 | 2019 백필 여부 결정 |
| 19 | **`expected_holding_bars`가 문서에만 있고 설정에는 없음** — 2/6/12/24h 표를 모두 생성했으나 런타임 비용 게이트가 사용할 단일 기준은 미확정 | 사용자/Phase 4 | edge table 연동 |
| 6 | D9 도입으로 `close_reason` 분포가 D4 가정과 달라짐 | Phase 4에서 재확인 | — |
| 5 | 슬리피지 모델 계수 (D9 에서 사이징에 직접 영향) | Phase 8 실측으로 보정 | — |
| ~~9~~ | ~~로컬 `.venv` 실행 불가~~ → sandbox 외부 프로젝트 가상환경으로 전체 검증 성공 | — | 해소됨 |
| 10 | **JEV 도입 역할 미정** — shadow 판단기(권장) / 진입 veto / 신규 신호 중 선택 필요 | 사용자 | 현재 Phase 2 비블로킹 |

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
- [x] 검증할 시장 가설 1문장 (D11, 가설 H1)
- [x] 보유시간 상한 제거 + 반대 점수 역전 청산 (D12)

### Phase 1 — 기반
- [x] `mypy --strict` / ruff / pytest 통과 (159 passed, 0 errors)
- [x] `schema_version` + `v_daily_pnl` / `v_daily_equity` 뷰 (D10)
- [x] 대시보드 읽기 전용 보장 (INSERT/UPDATE/CREATE 전부 거부 확인)
- [ ] `account_snapshots` 1분 주기 기록
- [x] import-linter Pure Core 경계 계약 통과
- [x] 아키텍처 테스트가 실제 위반을 탐지함을 확인
- [x] **2020-01-01~2026-09-19 전 6개 TF 적재** (1m 3,533,760봉 = 2,454일×1440, 갭 0건)
- [x] 1m → 상위 TF 재집계 대조: 706,752 버킷, **원인 미규명 불일치 0건**
- [x] 설정 해시 재현성 테스트 통과 (주석·키 순서 무관, 50회 반복 동일)
- [x] D9 파라미터 정합성 검증이 기동을 막는지 확인
- [x] 설정 파일에 시크릿이 들어가면 거부

### Phase 2 — 전략 코어
- [x] `decide()` 가 I/O 없이 동작 (import-linter 통과)
- [x] 동일 입력 → 동일 Decision 해시 (1000회)
- [x] 전 게이트에 통과/거부 테스트 존재
- [x] feature 상관행렬 |corr| < 0.7 (실측 최대 0.392)

### Phase 3 — 백테스트
- [x] 합성 데이터에서 체결·수수료·funding 이 수기 계산과 일치
- [x] 강제청산 시뮬레이션 동작
- [x] 동일 run_id 재실행 시 지표 완전 일치 (골든 digest + 파일 바이트 비교)
- [x] 리포트 전 항목 생성 (JSON/Markdown/CSV/PNG 13종)

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
| 2026-09-21 | Claude Opus 5 | 0 | D9 파라미터 조정: max_leverage 5→10, min_stop_distance 2%→1% | 완료 |
| 2026-09-21 | Claude Opus 5 | 0 | 대시보드·exe 설계 추가 (D10, 14번 문서). Phase 1 스키마에 집계 뷰 반영 | D10 확정 |
| 2026-09-21 | Claude Opus 5 | 1 | config 모듈 + base/backtest/paper/testnet/live yaml + 테스트 91건 | 완료 |
| 2026-09-21 | Claude Opus 5 | 1 | storage 모듈 (스키마 23테이블 + 뷰 3종, 마이그레이션, repository) + 테스트 72건 | 완료 |
| 2026-09-21 | Claude Opus 5 | 1 | marketdata 아카이브 클라이언트 + 백필 오케스트레이터 + 테스트 55건 | 완료 |
| 2026-09-21 | Claude Opus 5 | 1 | resample + integrity 검증기 + 게이트1 통합 테스트 (총 97건) | 완료 |
| 2026-09-21 | Claude Opus 5 | 1 | scripts/backfill.py 작성 + 실서버 1개월 스모크 (재집계 전건 일치) | 완료 |
| 2026-09-21 | Claude Opus 5 | 0 | 시장 가설 H1 확정 (D11) — Phase 0 완료 | D11 확정 |
| 2026-09-21 | Claude Opus 5 | 1 | 전체 백필 실행 (414만봉 / 878MB / 13.7분) | 완료 |
| 2026-09-21 | Claude Opus 5 | 1 | 재집계 불일치 78건 원인 규명 → 거래소 아카이브 특성으로 확인, 분류 로직 추가 | **게이트 1 통과** |
| 2026-09-21 | Claude Opus 5 | 2 | features/normalize.py (causal z-score/백분위) + 테스트 50건 | 완료 |
| 2026-09-21 | Claude Opus 5 | 2 | squash 를 tanh → x/(k+\|x\|) 로 변경 (플랫폼 결정론) | 완료 |
| 2026-09-21 | Claude Opus 5 | 2 | indicators (SMA/EMA/ATR/RSI) + 지표 5종 + 테스트 132건 | 완료 |
| 2026-09-21 | Claude Opus 5 | 2 | **B1 정규화 결함 발견·수정**: z-score 는 지속 추세에서 0 수렴 → ATR 단위로 변경 | 완료 |
| 2026-09-21 | Claude Opus 5 | 2 | structure.py ZigZag 스윙 + repainting 차단 테스트 32건 | 완료 |
| 2026-09-21 | Codex | 2 | D9 PositionSizingEngine + G1~G7 리스크 게이트 + 테스트 80건 | 완료 |
| 2026-09-21 | Codex | 2 | causal/repainting 테스트 9건에 leakage marker 연결 | 완료 |
| 2026-09-21 | Codex | 0/2 | D12 무제한 보유·점수 역전 청산 확정, 설정/리스크 정합화 + strategy policy 테스트 | 완료 |
| 2026-09-21 | Codex | 0/2 | D13 단일 squash 확정, bias/trigger/scoring/cost + 순수 decide() 및 결정론 테스트 | 완료 |
| 2026-09-21 | Claude Opus 5 | 3 | 인메모리 feed·체결·portfolio·이벤트 재생 엔진 및 백테스트 테스트 작성 | 미커밋 인계 |
| 2026-09-21 | Codex | 3 | Opus 작업 인계 검증, 누적 RiskState·정산시점 funding·stale funding 보정, 실데이터 2주 스모크 | 완료 |
| 2026-09-21 | Codex | 3 | 전 성과지표·causal 국면분해·리포트 13종 + 동일 run_id 골든 테스트 구현 | 완료 |
| 2026-09-21 | Codex | 3 | Buy & Hold·랜덤 1,000회·단일 feature 기준선 및 리포트 연동 | **게이트 3 통과** |
| 2026-09-21 | Claude | 2 | Codex 산출물 검토 — 구조 손절 미연결·기록/수식 불일치·Component 불변식 충돌 3건 수정 | 완료 |
| 2026-09-21 | Claude | 2 | `features/registry.py` + `marketdata/snapshot.py` + 테스트 30건 | 완료 |
| 2026-09-21 | Claude | 2 | 실데이터 `decide()` 스윕 2,184회 — 진입 16건 실증, 상관행렬 통과, 미해결 #11~#14 발견 | 완료 |
| 2026-09-21 | Claude | 0/2 | **D14 확정·구현** — T1 을 시간적 setup 으로, 국면계수를 사이징으로 이전 | 완료 |
| 2026-09-21 | Claude | 2 | D13 vs D14 동일데이터 비교 (2023·2024·2025) — 우열 판정 불가, 노이즈 범위 | 완료 |
| 2026-09-21 | Claude | 2/3 | funding 수집 — 7,305건 적재, 게이트 차단 해소. 지터 오탐 2,256건 방지 | 완료 |
| 2026-09-22 | Claude | 3 | 백테스트 엔진·체결·회계 + digest 캐시(148→6ms) + B2 캐시 | 완료 |
| 2026-09-22 | Claude | 4 | Codex H1 판정 독립 검증 — 지지. 24h 측정 불가 발견 → D15 | 완료 |
| 2026-09-22 | Claude | 0/4 | H2 제안·사전 등록, metrics 수집 222,694건, 선별 실행 | 완료 |
| 2026-09-22 | Claude | 4 | F5 단독 VALIDATION(2024) 재현 검정 → 실패, H2 기각. 접근 재검토 단계 | 완료 |
| 2026-09-22 | Claude | 4 | post-only 비용 실측(무작위 시점) — 순개선 4bps 로 보였음 | 철회 |
| 2026-09-22 | Claude | 4 | 다중 심볼 전환(D18) — 검정력 확보 확인, H3 사전 등록 | 완료 |
| 2026-09-22 | Claude | 4 | 시점별 유니버스 272종 구성 + 교차 심볼 평가 모듈 | 완료 |
| 2026-09-22 | Claude | 4 | **수집기 동시 실행 실패** — 전량 제출로 메모리 6.7GB, 단일 트랜잭션으로 잠금 충돌 | 실패 |
| 2026-09-22 | Claude | 4 | `marketdata/bulk.py` 배치 루프로 수정 (배치 커밋·재개·WAL 절단) | 완료 |
| 2026-09-22 | Claude | 4 | **F5 신호 시점 재측정 — 역선택 27배, maker 가 taker 에 패배. 선택지 B 종료** | 완료 |
| 2026-09-22 | Codex | 4 | 가용 TRAIN 2020~2022 다중 horizon edge table 추정 — 전체 평균 대부분 음수, VALIDATION/TEST 미접근 | 완료 |
| 2026-09-22 | Codex | 4 | 공통 `evaluate_scores()` 경로·병렬 추정기·fail-closed 버킷 테스트, 926 tests/39 leakage 통과 | 완료 |

| 2026-09-22 | Claude | 4 | 유니버스 지표 수집 완료 — 1,752작업 · 1,524만건 (파일없음 1,437일) | 완료 |
| 2026-09-22 | Claude | 4 | **funding 이 BTCUSDT 1종뿐임을 선별 직전 발견** — 그대로 돌렸으면 X1 오탈락 | 발견 |
| 2026-09-22 | Claude | 4 | 유니버스 funding 수집기 + 선별기 데이터 전제조건 검사(90%) 추가 | 완료 |
| 2026-09-22 | Claude | 4 | 사전 등록 "최소 표본" 을 200 으로 해석 확정 — **결과 열람 전** 기록 | 완료 |
| 2026-09-22 | Claude | 4 | H3 TRAIN 선별 완료 — 15개 조합 모두 22bps 비용 기준 실패, 결과 문서 작성 | **H3 기각** |
| 2026-09-22 | Codex | 4 | Opus 결과 인계 검증, 사전등록 종합 판정 누락 보완·테스트, D19·진행 기록 확정 | 1,041 tests · 완료 |

> **작성 규칙**: 작업 종료 시 이 표에 한 줄 추가. `날짜 / 에이전트 / Phase / 무엇을 / 결과`.
> 실패한 작업도 기록한다 — 같은 실패를 반복하지 않기 위해서다.
