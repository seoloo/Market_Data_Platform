# 02 — 아키텍처 / 데이터 흐름 / 모듈 구조 (B, C, D)

## 1. 최상위 설계 원칙

### 원칙 1 — Pure Core

전략과 리스크 판단은 **순수 함수**로 구현한다.

```
decide(snapshot: MarketSnapshot, portfolio: PortfolioState, cfg: Config) -> Decision
```

- 시스템 시계(`datetime.now()`), 네트워크, DB, 파일, 난수에 **접근하지 않는다**
- 시각이 필요하면 `snapshot.as_of_ms` 를 쓴다
- 이 규칙 하나가 백테스트-실거래 일치성의 90%를 담당한다

> 백테스트와 라이브가 "비슷한 로직"을 각각 구현하는 순간 그 시스템은 실패한다.
> 동일한 함수를 호출해야 한다.

### 원칙 2 — 3중 안전망

| 층 | 위치 | 동작 | 프로세스가 죽으면? |
|---|---|---|---|
| L1 Pre-trade gates | 프로세스 내 (순수) | 진입 거부 | 무효 (대신 진입도 안 됨) |
| L2 Exchange-side stop | **거래소 서버** | 손절 체결 | **계속 유효** |
| L3 Watchdog / Kill switch | 별도 프로세스 | 감시·강제정지 | 별도 프로세스라 유효 |

L2가 핵심이다. 로컬 손절 로직만 있으면 프로세스 사망 = 무방비 포지션이다.

### 원칙 3 — 거래소가 진실의 원천

로컬 DB는 캐시이자 기록이다. 불일치 시 **항상 거래소 상태를 채택**하고, 불일치 사실 자체를 `risk_events` 에 남긴다.

### 원칙 4 — Fail-closed

판단에 필요한 데이터가 stale/결측이면 **진입을 거부**한다.
"데이터가 없으니 중립 점수 0" 은 금지한다. 중립 점수는 "진입해도 된다"는 신호로 오작동할 수 있다.

---

## 2. 시스템 아키텍처

```
                         +--------------------------------------+
                         |          외부 세계 (I/O)              |
                         +--------------------------------------+
  Binance REST/WS ------>| ExchangeAdapter                      |
  FRED / BLS      ------>| MacroSource                          |
  News API / RSS  ------>| NewsSource                           |
  Anthropic API   ------>| LLMClient                            |
  Telegram        <------| Notifier                             |
                         +------------------+-------------------+
                                            |  (정규화된 DTO)
                         +------------------v-------------------+
                         |      Ingestion & Storage             |
                         |  candles / funding / macro / news    |
                         |  -> SQLite(WAL)                      |
                         +------------------+-------------------+
                                            |
                         +------------------v-------------------+
                         |      SnapshotBuilder                 |
                         |  as_of 이전 데이터만으로 구성 (causal)|
                         +------------------+-------------------+
                                            |
      ==============================================================
      ||  PURE CORE  (I/O 없음, 결정론적, 백테스트/라이브 공용)    ||
      ||                                                          ||
      ||   FeatureEngine -> ScoringEngine -> EntryExitPolicy      ||
      ||                                          |               ||
      ||                                          v               ||
      ||                                      CostGate            ||
      ||                                          v               ||
      ||                                 RiskEngine (veto)        ||
      ||                                          v               ||
      ||                                   PositionSizer          ||
      ||                                          v               ||
      ||                                 Decision + Intent[]      ||
      ==============================================================
                                            |
                 +--------------------------+--------------------------+
                 |                                                     |
       +---------v----------+                             +------------v-----------+
       |  LiveExecutor      |                             |  BacktestExecutor      |
       |  OrderManager      |                             |  FillSimulator         |
       |  PositionManager   |                             |  (fee/slippage/funding)|
       |  Reconciler        |                             +------------+-----------+
       +---------+----------+                                          |
                 |                                                     |
                 +---------------------+-------------------------------+
                                       v
                               DecisionJournal (DB)
                                       |
                        +--------------+--------------+
                        v                             v
                 Telegram Notifier            Metrics / Report
```

**Watchdog** 은 위 그림 밖의 별도 프로세스로, DB 하트비트와 거래소 포지션을 독립적으로 조회하여
"OPEN 포지션인데 보호주문 없음", "메인 프로세스 무응답" 등을 감지한다.

**Dashboard** 도 별도 프로세스다. DB 를 **읽기 전용**(`mode=ro`)으로만 열고
`127.0.0.1` 에만 바인딩하며, 쓰기 엔드포인트가 하나도 없다 (D10 / [14](14-ui-and-packaging.md)).

```
  매매 프로세스 ──write──▶  SQLite (WAL)  ◀──read only──  대시보드 프로세스
       ▲                         ▲                              │
       └──── 워치독이 감시 ───────┘                        브라우저(localhost)
```

---

## 3. 데이터 흐름 (C)

### 3.1 라이브 — Slow path (전략 평가, 1분 주기)

```
t = 00:00.000  1분봉 마감
  |
  v
[1] WS kline(closed=true) 수신, 또는 t+2s 에 REST 폴백
  |
[2] CandleStore.upsert()  ->  무결성 검사 (갭 / 중복 / 시각 정렬)
  |
[3] 갭 발견 시 REST backfill -> 복구 불가면 DEGRADED (신규진입 차단)
  |
[4] SnapshotBuilder.build(as_of = 봉 마감 시각)
  |     - 각 TF에서 as_of 이하의 "마감된" 봉만 포함
  |     - 데이터 신선도(staleness) 플래그 부착
  |
[5] PURE CORE: decide(snapshot, portfolio, config) -> Decision
  |     - Decision 에 veto 사유, 점수 분해, 비용 추정이 모두 포함
  |
[6] DecisionJournal.write(decision)   <- 주문 전송 여부와 무관하게 항상 기록
  |
[7] Intent 가 있으면 OrderManager.execute(intent)
  |     - clientOrderId 결정론적 생성
  |     - 중복 검사 (DB + 거래소)
  |
[8] 체결 결과 -> PositionManager 상태 전이 -> 보호주문 재정렬
  |
[9] 상태 변화 시 Telegram 알림
```

목표 소요: **5초 이내** (NFR-1)

### 3.2 라이브 — Fast path (보호, 상시)

```
WS markPrice / userDataStream
  |
  +--> ORDER_TRADE_UPDATE  -> 주문/포지션 상태 즉시 반영
  +--> ACCOUNT_UPDATE      -> 잔고/포지션 반영
  +--> 이상 감지 (가격 스파이크, 데이터 stale) -> DEGRADED 전환
```

Fast path는 **신규 진입을 하지 않는다.** 보호와 상태 동기화만 담당한다.
실제 손절 체결은 거래소 측 `STOP_MARKET` 이 수행한다.

### 3.3 라이브 — Reconcile path

기동 시 / 주기적(예: 60초) / 불일치 이벤트 발생 시 → [04-state-machine.md](04-state-machine.md) §4

### 3.4 백테스트 흐름

```
for bar_close_ts in timeline(1m):                        # 1분 단위 전진
    snapshot = SnapshotBuilder.build(as_of=bar_close_ts, source=HistoricalStore)
    decision = decide(snapshot, portfolio, config)       # <- 라이브와 동일 함수
    FillSimulator.process(decision.intents, next_bar)    # 다음 봉에서 체결
    FillSimulator.resolve_protective(next_1m_bars)       # 봉 내부 경로 해소
    FundingAccrual.apply_if_due(bar_close_ts)
    DecisionJournal.write(decision)
```

**핵심**: `decide()` 는 라이브와 백테스트가 문자 그대로 같은 함수다.
다른 것은 `SnapshotBuilder` 의 데이터 소스와 `Executor` 뿐이다.

### 3.5 느린 배치 흐름 (뉴스 / 거시 — MVP 이후)

```
[스케줄러] -> NewsCollector -> Dedup -> 관련성 필터(규칙) -> 중요도 필터
                                                              |
                                        상위 N건만 -> LLM 분석 -> news_analysis (DB)
                                                              |
                                                              v
                                     다음 봉 마감 평가 시 Snapshot 에 포함
```

뉴스는 **즉시 주문을 유발하지 않는다.** 항상 다음 봉 마감 평가를 거친다. (→ 12-critique §5)

---

## 4. 모듈 구조 (D)

원 제안에서 바꾼 부분은 아래 표에 이유와 함께 정리했다.

```
Auto_Trading/
├── main.py                     # 엔트리포인트 (얇게 유지)
├── watchdog.py                 # 별도 프로세스 엔트리포인트
├── dashboard.py                # 대시보드 엔트리포인트 (D10, 읽기 전용)
├── pyproject.toml
├── .env.example                # 실제 .env 는 gitignore
├── config/
│   ├── base.yaml
│   ├── backtest.yaml           # 오버레이
│   ├── paper.yaml
│   ├── testnet.yaml
│   └── live.yaml
├── docs/phase0/
├── data/                       # gitignore (DB, 캔들 캐시)
└── src/auto_trading/
    ├── core/                   # [신설] 순수 도메인. 외부 의존 최소
    │   ├── types.py            #   MarketSnapshot, Decision, Intent, Order, Position (frozen dataclass)
    │   ├── enums.py            #   Side, OrderType, OrderState, PositionState, VetoReason
    │   ├── clock.py            #   Clock 프로토콜 (RealClock / SimClock)
    │   ├── money.py            #   Decimal 기반 수량/가격, tickSize/stepSize 반올림
    │   └── errors.py
    ├── config/
    │   ├── schema.py           # pydantic 모델 — 설정 검증 (leverage=100 같은 오타 차단)
    │   ├── loader.py           # yaml + env 오버레이, 해시 계산
    │   └── registry.py         # config_version 저장/조회
    ├── storage/
    │   ├── db.py               # 커넥션, WAL, 마이그레이션 러너
    │   ├── migrations/
    │   ├── models.py
    │   └── repositories/       # candles, decisions, orders, positions, trades ...
    ├── exchange/
    │   ├── base.py             # ExchangeAdapter 프로토콜 (좁게 유지)
    │   ├── binance/
    │   │   ├── rest.py
    │   │   ├── ws.py
    │   │   ├── mapping.py      # 거래소 코드 -> 내부 enum
    │   │   └── filters.py      # tickSize / stepSize / minNotional 처리
    │   ├── paper.py            # 가상 체결 어댑터 (동일 인터페이스)
    │   └── ratelimit.py
    ├── marketdata/
    │   ├── collector.py        # WS + REST 수집
    │   ├── backfill.py         # 과거 데이터 다운로드
    │   ├── integrity.py        # [신설] 갭 / 중복 / 스파이크 검증
    │   └── snapshot.py         # [신설] SnapshotBuilder — causal 보장 단일 지점
    ├── features/               # indicators -> features 로 개명
    │   ├── base.py             #   Feature 프로토콜: compute() + warmup_bars
    │   ├── trend.py
    │   ├── momentum.py
    │   ├── volatility.py
    │   ├── volume.py
    │   ├── structure.py        #   swing HH/HL/LH/LL, S/R, 피보나치
    │   └── normalize.py        # [핵심] causal z-score / percentile rank
    ├── strategy/
    │   ├── signals.py          # feature -> signal [-1,+1]
    │   ├── bias.py             # [신설] 느린 층 (4h/1d/거시) -> 허용 방향 + 사이즈 배수
    │   ├── trigger.py          # [신설] 빠른 층 (15m/5m/1m) -> 진입 타이밍
    │   ├── scoring.py          # 합성 -> direction / confidence / risk_score
    │   ├── policy.py           # 진입 임계값, 점수 역전 청산, 쿨다운
    │   ├── cost.py             # expected_edge / expected_cost 게이트
    │   └── pipeline.py         # decide() — 순수 진입점
    ├── risk/
    │   ├── gates.py            # 개별 게이트 (순수, 단위테스트 용이)
    │   ├── engine.py           # 게이트 조합 + 최종 veto
    │   ├── sizing.py           # 위험기반 포지션 사이징
    │   └── limits.py           # 일일손실 / DD / 연속손실 상태 추적
    ├── execution/
    │   ├── order_manager.py    # 멱등 주문, 재시도 정책
    │   ├── position_manager.py # 포지션 상태머신
    │   ├── protective.py       # [신설] SL/TP/trailing 등록·재정렬 보증
    │   ├── reconciliation.py   # 기동 / 주기 정합성 복구
    │   └── idempotency.py      # clientOrderId 생성 규칙
    ├── backtest/
    │   ├── engine.py
    │   ├── fills.py            # 체결 시뮬 (슬리피지 모델 포함)
    │   ├── funding.py
    │   ├── metrics.py
    │   ├── walkforward.py
    │   ├── regimes.py          # 시장 국면 라벨링
    │   └── report.py
    ├── macro/                  # MVP 이후
    │   ├── calendar.py         # 발표 일정 (MVP: 블랙아웃 용도로만)
    │   ├── sources.py          # FRED / BLS
    │   └── scoring.py
    ├── news/                   # MVP 이후
    │   ├── collector.py
    │   ├── dedup.py
    │   ├── filters.py          # 규칙 기반 1차 필터
    │   └── analyzer.py
    ├── ai/                     # MVP 이후
    │   ├── client.py
    │   ├── prompts.py
    │   ├── schemas.py          # 구조화 출력 스키마 + 검증
    │   └── cache.py            # 동일 입력 -> 캐시 (비용 + 결정론)
    ├── notify/
    │   ├── telegram.py
    │   ├── templates.py
    │   └── daily_report.py
    ├── ui/                     # [신설] 대시보드 — 읽기 전용, 별도 프로세스 (D10)
    │   ├── app.py              #   FastAPI
    │   ├── db.py               #   mode=ro 커넥션 + query_only
    │   ├── queries.py          #   집계 SQL (매매 로직 없음)
    │   ├── routes/             #   summary / equity / calendar / trades / diagnostics
    │   ├── templates/
    │   └── static/             #   css, js, vendor/chart.min.js (CDN 금지)
    ├── ops/                    # [신설]
    │   ├── health.py           # 헬스체크 / 하트비트
    │   ├── killswitch.py
    │   ├── state.py            # RUNNING / DEGRADED / HALTED
    │   └── logging.py          # 구조화 로깅
    └── app/
        ├── scheduler.py        # 봉 마감 정렬 스케줄러
        ├── live_runner.py
        └── wiring.py           # 의존성 조립 (DI)
```

### 원 제안 대비 변경점

| 변경 | 이유 |
|---|---|
| `core/` 신설 | 순수 타입·시계 추상화를 한곳에 모아 "I/O 금지" 경계를 물리적으로 명확히 함 |
| `indicators/` → `features/` + `normalize.py` 분리 | 지표 계산보다 **정규화가 look-ahead 누출의 실제 발생 지점**. 별도 모듈로 격리해 집중 테스트 |
| `marketdata/snapshot.py` 신설 | causal 보장을 단일 지점에 집중. 여기만 검증하면 누출을 구조적으로 차단 |
| `marketdata/integrity.py` 신설 | 잘못된 시장 데이터는 09 문서의 상위 위험. 검증을 수집 경로에 강제 |
| `strategy/bias.py` + `trigger.py` 분리 | 시간 스케일이 다른 신호를 단일 가중합으로 뭉개는 문제 회피 (→ 12-critique §4) |
| `execution/protective.py` 신설 | "OPEN인데 스톱 없음" 상태를 구조적으로 금지하는 전용 모듈 |
| `execution/idempotency.py` 신설 | 중복 진입 방지의 핵심. 단독 테스트 대상으로 격리 |
| `elliott/` 제거 → `features/structure.py` 흡수 | MVP 제외. 재현 가능한 부분(swing / 피보나치)만 흡수 (→ 12-critique §3) |
| `ops/` 신설 | 킬스위치·상태·헬스를 전략 코드에서 분리 |
| `exchange/paper.py` | Paper trading을 별도 모드가 아니라 **어댑터 교체**로 구현 → 코드 경로 단일화 |
| `config/schema.py` (pydantic) | 설정 오타로 인한 사고 차단 (레버리지·비율 범위 검증) |
| `ui/` 신설 + `dashboard.py` | 결과 확인용 대시보드. **별도 프로세스 + 읽기 전용 DB** 라 UI 장애가 매매에 닿지 않음 (D10) |

### 의존 방향 규칙 (CI에서 강제)

```
core  <-  features  <-  strategy  <-  risk
  ^                                    ^
  +---------- execution ---------------+
                  ^
    exchange / storage / marketdata / notify / ai
```

- `core`, `features`, `strategy`, `risk` 는 `exchange`, `storage`, `marketdata`, `notify`, `ai` 를 **import 할 수 없다**
- `ui` 는 `core` 와 `storage` 만 import 한다. `strategy`, `risk`, `exchange`, `execution`, `ai` 는 **금지** (D10)
- CI에서 `import-linter` 로 검사한다
- 이 규칙이 깨지면 백테스트-실거래 일치성이 **조용히** 무너진다. 그래서 사람 리뷰가 아니라 린터로 강제한다
