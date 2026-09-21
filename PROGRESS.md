# PROGRESS.md — 작업 현황

> **모든 AI 에이전트는 작업 시작 전 이 파일을 읽고, 작업 종료 시 반드시 갱신한다.**
> 대화 기록은 사라지지만 이 파일은 남는다. 여기 없는 것은 진행되지 않은 것이다.

**최종 갱신**: 2026-09-22
**갱신자**: Codex
**저장소 상태**: e944a6e + 미커밋 작업물 (Phase 4 24h 비중첩 edge 진단)

---

## 📍 현재 위치

```
Phase 0 — 설계     ████████████████████  100%
Phase 1 — 기반     ████████████████████  100%  ✅ 게이트 1 통과
Phase 2 — 전략코어 ████████████████████ 100%  ✅ 게이트 2 통과
Phase 3 — 백테스트 ████████████████████ 100%  ✅ 게이트 3 통과
Phase 4 — 검증 ★   █████░░░░░░░░░░░░░░░  25%  ← 현재
Phase 5 — 거래소   ░░░░░░░░░░░░░░░░░░░░   0%
Phase 6 — Paper    ░░░░░░░░░░░░░░░░░░░░   0%
Phase 7 — Testnet  ░░░░░░░░░░░░░░░░░░░░   0%
Phase 8 — 소액실거래░░░░░░░░░░░░░░░░░░░░   0%
Phase 9 — 실거래   ░░░░░░░░░░░░░░░░░░░░   0%
```

Phase 정의: [`docs/phase0/10-roadmap.md`](docs/phase0/10-roadmap.md)

---

## ➡️ 다음 작업

**Phase 4 — 전략 검증 진행 중 (TRAIN만 접근)**

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

### Phase 4 — 전략 검증 (2026-09-22, 진행 중)

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
| 2026-09-22 | Codex | 4 | 가용 TRAIN 2020~2022 다중 horizon edge table 추정 — 전체 평균 대부분 음수, VALIDATION/TEST 미접근 | 진행 중 |
| 2026-09-22 | Codex | 4 | 공통 `evaluate_scores()` 경로·병렬 추정기·fail-closed 버킷 테스트, 926 tests/39 leakage 통과 | 완료 |

> **작성 규칙**: 작업 종료 시 이 표에 한 줄 추가. `날짜 / 에이전트 / Phase / 무엇을 / 결과`.
> 실패한 작업도 기록한다 — 같은 실패를 반복하지 않기 위해서다.
