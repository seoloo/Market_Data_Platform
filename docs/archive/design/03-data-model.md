> STATUS: HISTORICAL / NOT CURRENT
> 보관일: 2026-09-23 · 원래 경로: `docs/phase0/03-data-model.md`
> 승인된 문서 정리 직전 작업본. D22 및 대체된 결정을 현행 사양으로 사용하지 않는다.
> 본문은 보존하고 Markdown 상대 링크만 새 위치에 맞췄다. 현재 기준: [CURRENT_SPEC](../../current/CURRENT_SPEC.md).

# 03 — DB 스키마 초안 (E)

## 1. DB 선택

| 단계 | DB | 이유 |
|---|---|---|
| MVP ~ 소액 실거래 | **SQLite (WAL 모드)** | 단일 프로세스 + 워치독 읽기. 운영 부담 0. 백업 = 파일 복사 |
| 확장 시 | PostgreSQL (+ TimescaleDB) | 다중 심볼·다중 전략·동시 접근 |

- 저장소 접근은 전부 `storage/repositories/` 뒤에 숨긴다 → 나중에 교체 가능
- SQLite 설정: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`
- **금액/수량은 `TEXT`(Decimal 문자열) 또는 정수 스케일드로 저장.** REAL(부동소수점) 금지 — 수량 정밀도 오류가 주문 거부·dust 포지션을 만든다

## 2. 재현성 설계 (가장 중요한 부분)

"왜 그 거래가 발생했는가"를 재구성하려면 **결정 시점의 입력·설정·코드가 모두 고정**되어야 한다.

세 개의 고정점을 둔다:

| 고정점 | 테이블 | 내용 |
|---|---|---|
| **설정** | `config_versions` | 전체 config의 정규화 JSON + SHA-256 해시 |
| **코드** | `code_versions` | git commit sha + dirty 여부 + 주요 의존성 버전 |
| **입력** | `decisions.snapshot_digest` | MarketSnapshot의 정규화 직렬화 해시 |

`decisions` 한 행에 이 셋의 FK가 모두 들어간다.
→ 과거 결정 재현 = 같은 코드 체크아웃 + 같은 config + 같은 스냅샷 → `decide()` 재실행 → 출력 해시 일치 검증.

### 저장량 관리

1분마다 결정을 기록하면 연 ~52.6만 행. 컴포넌트 분해 JSON을 매번 저장하면 연 수백 MB가 된다.

정책:
- `decisions` 는 **매 평가마다** 기록하되 숫자 컬럼은 좁게 유지
- `decisions.components_json` 은 다음 경우에만 채운다:
  - 상태 변화 발생 (진입/청산/보호주문 변경)
  - Risk Engine veto 발생
  - `|direction|` 이 actionable 밴드(config) 안에 들어옴
  - 매 N분마다 1회 (기본 60) — 샘플링 기록
- 그 외에는 `components_json = NULL`, 대신 `snapshot_digest` 가 있으므로 **재계산으로 복원 가능**

---

## 3. 스키마

> 아래는 SQLite DDL 초안이다. `*_ms` 는 UTC epoch milliseconds.

### 3.1 기준/메타

```sql
-- exe 와 DB 버전 불일치를 감지한다 (D10 §4.3).
-- 조용히 잘못 읽는 것이 최악이므로 기동 시 검사하고 불일치면 중단한다.
CREATE TABLE schema_version (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    version         INTEGER NOT NULL,
    applied_ms      INTEGER NOT NULL
);

CREATE TABLE config_versions (
    id              INTEGER PRIMARY KEY,
    hash            TEXT NOT NULL UNIQUE,          -- SHA-256 of canonical JSON
    config_json     TEXT NOT NULL,
    created_ms      INTEGER NOT NULL,
    note            TEXT
);

CREATE TABLE code_versions (
    id              INTEGER PRIMARY KEY,
    git_sha         TEXT NOT NULL,
    dirty           INTEGER NOT NULL,              -- 0/1, 1이면 실거래 금지
    deps_json       TEXT NOT NULL,
    created_ms      INTEGER NOT NULL,
    UNIQUE(git_sha, dirty, deps_json)
);

CREATE TABLE instruments (
    symbol          TEXT PRIMARY KEY,              -- 'BTCUSDT'
    exchange        TEXT NOT NULL,
    contract_type   TEXT NOT NULL,                 -- 'PERPETUAL'
    tick_size       TEXT NOT NULL,
    step_size       TEXT NOT NULL,
    min_qty         TEXT NOT NULL,
    min_notional    TEXT NOT NULL,
    max_leverage    INTEGER NOT NULL,
    fetched_ms      INTEGER NOT NULL
);
```

### 3.2 시장 데이터

```sql
CREATE TABLE candles (
    exchange        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,                 -- '1m','5m','15m','1h','4h','1d'
    open_ms         INTEGER NOT NULL,              -- 봉 시작 (UTC)
    close_ms        INTEGER NOT NULL,              -- 봉 마감 (UTC), = open_ms + tf - 1
    open            TEXT NOT NULL,
    high            TEXT NOT NULL,
    low             TEXT NOT NULL,
    close           TEXT NOT NULL,
    volume          TEXT NOT NULL,
    quote_volume    TEXT NOT NULL,
    trade_count     INTEGER,
    taker_buy_base  TEXT,
    source          TEXT NOT NULL,                 -- 'ws' | 'rest' | 'bulk'
    ingested_ms     INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol, timeframe, open_ms)
);
CREATE INDEX idx_candles_lookup ON candles(symbol, timeframe, open_ms DESC);

-- 5m 이상은 1m에서 파생 가능하지만, 거래소 원본을 그대로 저장한다.
-- 이유: 파생 집계 로직 버그가 백테스트 전체를 오염시키는 것을 막고,
--       거래소 원본과 파생값을 대조하는 무결성 테스트를 만들 수 있다.

CREATE TABLE candle_gaps (                         -- 무결성 검사 결과
    id              INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    missing_from_ms INTEGER NOT NULL,
    missing_to_ms   INTEGER NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0,
    detected_ms     INTEGER NOT NULL,
    note            TEXT
);

CREATE TABLE funding_rates (
    exchange        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    funding_ms      INTEGER NOT NULL,              -- 정산 시각 (00/08/16 UTC)
    rate            TEXT NOT NULL,
    mark_price      TEXT,
    PRIMARY KEY (exchange, symbol, funding_ms)
);

CREATE TABLE fee_schedule (                        -- 거래소 API에서 취득, 하드코딩 금지
    exchange        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    maker_bps       TEXT NOT NULL,
    taker_bps       TEXT NOT NULL,
    effective_ms    INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol, effective_ms)
);
```

### 3.3 판단 기록 (핵심)

```sql
CREATE TABLE decisions (
    id                  INTEGER PRIMARY KEY,
    as_of_ms            INTEGER NOT NULL,          -- 평가 기준 봉 마감 시각
    created_ms          INTEGER NOT NULL,          -- 실제 계산 시각 (지연 측정용)
    symbol              TEXT NOT NULL,
    mode                TEXT NOT NULL,             -- 'backtest'|'paper'|'testnet'|'live'
    run_id              TEXT NOT NULL,             -- 백테스트 실행 단위 / 라이브 세션 단위
    config_version_id   INTEGER NOT NULL REFERENCES config_versions(id),
    code_version_id     INTEGER NOT NULL REFERENCES code_versions(id),
    snapshot_digest     TEXT NOT NULL,             -- 입력 해시

    -- 점수 (06 문서 참조)
    direction           REAL NOT NULL,             -- [-1, +1]
    long_score          REAL NOT NULL,             -- 0..100
    short_score         REAL NOT NULL,             -- 0..100
    confidence_score    REAL NOT NULL,             -- 0..100
    risk_score          REAL NOT NULL,             -- 0..100
    bias_direction      INTEGER NOT NULL,          -- -1 | 0 | +1 (느린 층)
    bias_multiplier     REAL NOT NULL,
    agreement           REAL NOT NULL,             -- 컴포넌트 부호 일치도 0..1
    regime              TEXT,                      -- 'trend_up'|'trend_down'|'range'|'high_vol'...

    -- 비용/엣지 (06 문서 §5)
    expected_edge_bps   REAL,
    expected_cost_bps   REAL,
    required_edge_bps   REAL,

    -- 결론
    action              TEXT NOT NULL,             -- 'none'|'open_long'|'open_short'|'close'|'reduce'|'adjust_stop'
    veto_reasons        TEXT,                      -- JSON array, 비어있지 않으면 거부됨
    intent_json         TEXT,                      -- 생성된 Intent (있을 때)
    components_json     TEXT,                      -- 요소별 raw/normalized/weight/contribution (샘플링)
    data_quality_json   TEXT,                      -- TF별 staleness, 결측 여부
    latency_ms          INTEGER,

    UNIQUE (run_id, symbol, as_of_ms)              -- 동일 봉 중복 평가 방지 (멱등)
);
CREATE INDEX idx_decisions_time ON decisions(symbol, as_of_ms DESC);
CREATE INDEX idx_decisions_action ON decisions(action, as_of_ms DESC);
```

`components_json` 형태 예시:

```json
{
  "bias": {
    "trend_4h_ema_slope": {"raw": 0.0032, "z": 1.41, "signal": 0.62, "weight": 0.40, "contrib": 0.248},
    "trend_1h_adx_dir":   {"raw": 27.4,   "z": 0.88, "signal": 0.41, "weight": 0.30, "contrib": 0.123}
  },
  "trigger": {
    "pullback_15m_rsi":   {"raw": 38.2,  "z": -1.10, "signal": 0.55, "weight": 0.50, "contrib": 0.275},
    "vol_confirm_5m":     {"raw": 1.83,  "z": 0.94,  "signal": 0.33, "weight": 0.25, "contrib": 0.083}
  },
  "gates": {"volatility_regime": "normal", "event_blackout": false, "spread_bps": 0.8},
  "disabled": ["news", "macro_surprise", "elliott"]
}
```

### 3.4 주문 / 포지션 / 체결

```sql
CREATE TABLE intents (
    id              INTEGER PRIMARY KEY,
    decision_id     INTEGER NOT NULL REFERENCES decisions(id),
    created_ms      INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    purpose         TEXT NOT NULL,                 -- 'entry'|'exit'|'stop'|'take_profit'|'trail'|'flatten'
    side            TEXT NOT NULL,                 -- 'BUY'|'SELL'
    order_type      TEXT NOT NULL,                 -- 'MARKET'|'LIMIT'|'STOP_MARKET'|'TAKE_PROFIT_MARKET'
    qty             TEXT NOT NULL,
    price           TEXT,
    stop_price      TEXT,
    reduce_only     INTEGER NOT NULL DEFAULT 0,
    time_in_force   TEXT,
    client_order_id TEXT NOT NULL UNIQUE,          -- 결정론적 멱등 키
    state           TEXT NOT NULL                  -- 'pending'|'dispatched'|'settled'|'abandoned'
);

CREATE TABLE orders (
    id                  INTEGER PRIMARY KEY,
    intent_id           INTEGER REFERENCES intents(id),
    client_order_id     TEXT NOT NULL UNIQUE,
    exchange_order_id   TEXT,                      -- 최초에는 NULL 가능
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    order_type          TEXT NOT NULL,
    qty                 TEXT NOT NULL,
    price               TEXT,
    stop_price          TEXT,
    reduce_only         INTEGER NOT NULL,
    state               TEXT NOT NULL,             -- 04 문서 상태머신
    filled_qty          TEXT NOT NULL DEFAULT '0',
    avg_fill_price      TEXT,
    fee_paid            TEXT NOT NULL DEFAULT '0',
    fee_asset           TEXT,
    submitted_ms        INTEGER,
    last_update_ms      INTEGER NOT NULL,
    closed_ms           INTEGER,
    reject_reason       TEXT,
    raw_last_json       TEXT                       -- 거래소 원응답 (디버깅용)
);
CREATE INDEX idx_orders_state ON orders(state, last_update_ms);

CREATE TABLE fills (
    id                  INTEGER PRIMARY KEY,
    order_id            INTEGER NOT NULL REFERENCES orders(id),
    exchange_trade_id   TEXT NOT NULL,
    ts_ms               INTEGER NOT NULL,
    qty                 TEXT NOT NULL,
    price               TEXT NOT NULL,
    fee                 TEXT NOT NULL,
    fee_asset           TEXT NOT NULL,
    is_maker            INTEGER NOT NULL,
    realized_pnl        TEXT,
    UNIQUE (exchange_trade_id)                     -- 중복 이벤트 방어
);

CREATE TABLE positions (                           -- 포지션 "생애" 단위
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT NOT NULL,
    mode                TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    side                TEXT NOT NULL,             -- 'LONG'|'SHORT'
    state               TEXT NOT NULL,             -- 04 문서 상태머신
    opened_ms           INTEGER,
    closed_ms           INTEGER,
    entry_decision_id   INTEGER REFERENCES decisions(id),
    exit_decision_id    INTEGER REFERENCES decisions(id),
    qty                 TEXT NOT NULL DEFAULT '0',
    entry_avg_price     TEXT,
    exit_avg_price      TEXT,
    leverage_used       REAL,
    initial_stop_price  TEXT,
    current_stop_price  TEXT,
    take_profit_price   TEXT,
    planned_risk_usdt   TEXT,                      -- 진입 시 계획된 최대 손실
    close_reason        TEXT                       -- 'signal'|'stop'|'take_profit'|'risk_halt'|'manual'|'liquidation'
);

CREATE TABLE trades (                              -- 왕복(round-trip) 결과
    id                  INTEGER PRIMARY KEY,
    position_id         INTEGER NOT NULL UNIQUE REFERENCES positions(id),
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    opened_ms           INTEGER NOT NULL,
    closed_ms           INTEGER NOT NULL,
    holding_ms          INTEGER NOT NULL,
    qty                 TEXT NOT NULL,
    entry_price         TEXT NOT NULL,
    exit_price          TEXT NOT NULL,
    gross_pnl           TEXT NOT NULL,
    entry_fee           TEXT NOT NULL,
    exit_fee            TEXT NOT NULL,
    funding_paid        TEXT NOT NULL,
    net_pnl             TEXT NOT NULL,
    realized_r          REAL,                      -- net_pnl / planned_risk_usdt  (R 배수)
    slippage_entry_bps  REAL,                      -- 계획가 대비 실제 체결가
    slippage_exit_bps   REAL,
    mae_bps             REAL,                      -- Maximum Adverse Excursion
    mfe_bps             REAL,                      -- Maximum Favorable Excursion
    close_reason        TEXT NOT NULL
);
```

> `realized_r`, `mae_bps`, `mfe_bps`, `slippage_*_bps` 는 원 명세에 없지만 필수에 가깝다.
> - `realized_r`: 금액이 아니라 R 배수로 봐야 사이징 변화와 무관하게 전략을 평가할 수 있다
> - `mae/mfe`: 손절폭·익절폭이 적절한지 사후 판단하는 유일한 근거
> - `slippage_*`: 백테스트 비용 모델을 실측으로 보정하는 입력 (Phase 8 필수)

### 3.5 계좌 / 리스크

```sql
CREATE TABLE account_snapshots (
    id                  INTEGER PRIMARY KEY,
    ts_ms               INTEGER NOT NULL,
    mode                TEXT NOT NULL,
    wallet_balance      TEXT NOT NULL,
    unrealized_pnl      TEXT NOT NULL,
    equity              TEXT NOT NULL,             -- wallet + unrealized
    available_balance   TEXT NOT NULL,
    equity_high_water   TEXT NOT NULL,             -- DD 계산 기준 (영속화 필수)
    source              TEXT NOT NULL              -- 'exchange'|'computed'
);
CREATE INDEX idx_acct_ts ON account_snapshots(mode, ts_ms DESC);

-- 기록 주기: 1분 (D10). 자산 곡선 해상도를 위해 Phase 1 부터 기록한다.
-- 나중에 UI 를 만들 때 과거 데이터를 복구할 방법이 없다.
-- 연 52.6만 행이지만 행이 작아 문제 없다.

CREATE TABLE risk_state (                          -- 단일 행 갱신 + 이력
    id                  INTEGER PRIMARY KEY,
    ts_ms               INTEGER NOT NULL,
    mode                TEXT NOT NULL,
    trading_day         TEXT NOT NULL,             -- 'YYYY-MM-DD' (UTC 기준 리셋)
    day_realized_pnl    TEXT NOT NULL,
    day_trade_count     INTEGER NOT NULL,
    consecutive_losses  INTEGER NOT NULL,
    drawdown_pct        REAL NOT NULL,
    halted              INTEGER NOT NULL,
    halt_reason         TEXT,
    cooldown_until_ms   INTEGER
);

CREATE TABLE risk_events (
    id                  INTEGER PRIMARY KEY,
    ts_ms               INTEGER NOT NULL,
    severity            TEXT NOT NULL,             -- 'info'|'warn'|'critical'
    kind                TEXT NOT NULL,             -- 'veto'|'halt'|'reconcile_mismatch'|'unprotected_position'|'killswitch'
    decision_id         INTEGER REFERENCES decisions(id),
    detail_json         TEXT NOT NULL,
    action_taken        TEXT
);
```

### 3.6 거시 / 뉴스 / JEV (D22 전략 입력, provenance 필수)

```sql
CREATE TABLE macro_events (
    id                  INTEGER PRIMARY KEY,
    indicator           TEXT NOT NULL,             -- 'CPI','CORE_CPI','PCE','NFP','FOMC_RATE',...
    scheduled_ms        INTEGER NOT NULL,          -- 발표 예정 시각
    released_ms         INTEGER,                   -- 실제 발표 시각
    period              TEXT,                      -- '2026-08'
    actual              TEXT,
    consensus           TEXT,
    previous            TEXT,
    revised_previous    TEXT,
    surprise            TEXT,                      -- actual - consensus
    surprise_z          REAL,                      -- 과거 surprise 분포 대비 z
    source              TEXT NOT NULL,
    -- point-in-time 보장: 같은 지표/기간이 나중에 수정되면 새 행을 추가하고 이전 행을 유지
    vintage_ms          INTEGER NOT NULL,          -- 이 값이 "알려진" 시각
    UNIQUE (indicator, period, vintage_ms)
);
```

> `vintage_ms` 가 핵심이다. FRED 등이 주는 최신 수정치를 백테스트에 쓰면
> "그 시점에는 알 수 없던 값"을 쓰는 것이 되어 look-ahead가 된다.
> 백테스트 조회는 항상 `WHERE vintage_ms <= as_of_ms` 로 필터링한다.

```sql
CREATE TABLE news_items (
    id                  INTEGER PRIMARY KEY,
    external_id         TEXT UNIQUE,
    source              TEXT NOT NULL,
    url                 TEXT,
    title               TEXT NOT NULL,
    summary             TEXT,
    published_ms        INTEGER NOT NULL,
    ingested_ms         INTEGER NOT NULL,
    content_hash        TEXT NOT NULL,             -- 중복 제거 1차
    simhash             TEXT,                      -- 근사 중복 제거
    cluster_id          TEXT,                      -- 동일 사건 묶음
    prefilter_score     REAL,                      -- 규칙 기반 관련성
    selected_for_llm    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE news_analyses (
    id                      INTEGER PRIMARY KEY,
    news_id                 INTEGER NOT NULL REFERENCES news_items(id),
    model                   TEXT NOT NULL,
    prompt_version          TEXT NOT NULL,
    analyzed_ms             INTEGER NOT NULL,
    relevance               REAL, importance REAL,
    bullish_score           REAL, bearish_score REAL,
    confidence              REAL,
    expected_duration_min   INTEGER,
    event_type              TEXT,
    duplicate_event_id      INTEGER,
    reason                  TEXT,
    raw_response_json       TEXT NOT NULL,
    input_digest            TEXT NOT NULL,         -- 동일 입력 재현/캐시 키
    UNIQUE (news_id, model, prompt_version)
);
```

### 3.7 운영

```sql
CREATE TABLE system_events (
    id          INTEGER PRIMARY KEY,
    ts_ms       INTEGER NOT NULL,
    kind        TEXT NOT NULL,      -- 'startup'|'shutdown'|'ws_disconnect'|'ws_reconnect'|'degraded'|'recovered'|'reconcile'
    detail_json TEXT
);

CREATE TABLE errors (
    id          INTEGER PRIMARY KEY,
    ts_ms       INTEGER NOT NULL,
    component   TEXT NOT NULL,
    error_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    traceback   TEXT,
    context_json TEXT,
    handled     INTEGER NOT NULL
);

CREATE TABLE heartbeats (           -- 워치독이 읽는다
    component   TEXT PRIMARY KEY,
    ts_ms       INTEGER NOT NULL,
    state       TEXT NOT NULL       -- 'RUNNING'|'DEGRADED'|'HALTED'
);

CREATE TABLE backtest_runs (
    run_id          TEXT PRIMARY KEY,
    created_ms      INTEGER NOT NULL,
    config_version_id INTEGER NOT NULL REFERENCES config_versions(id),
    code_version_id   INTEGER NOT NULL REFERENCES code_versions(id),
    data_from_ms    INTEGER NOT NULL,
    data_to_ms      INTEGER NOT NULL,
    split           TEXT NOT NULL,   -- 'train'|'validation'|'test'|'walkforward'
    metrics_json    TEXT,
    notes           TEXT
);
```

## 4. 대시보드용 집계 뷰 (D10)

[14-ui-and-packaging.md](14-ui-and-packaging.md) §3.3 참조.
**UI 구현은 Phase 6 이지만 뷰와 기록은 Phase 1 에 넣는다** — 기록하지 않은 데이터는 복구할 수 없다.

```sql
-- 날짜별 실현손익 (달력 화면). 기준 시간대 UTC = Risk Engine 일일 리셋과 동일
CREATE VIEW v_daily_pnl AS
SELECT
    date(t.closed_ms / 1000, 'unixepoch')        AS trade_date,
    p.mode,
    p.run_id,
    COUNT(*)                                      AS trade_count,
    SUM(CAST(t.net_pnl AS REAL))                  AS realized_pnl,
    SUM(CAST(t.gross_pnl AS REAL))                AS gross_pnl,
    SUM(CAST(t.entry_fee AS REAL) + CAST(t.exit_fee AS REAL)) AS fees,
    SUM(CAST(t.funding_paid AS REAL))             AS funding,
    SUM(CASE WHEN CAST(t.net_pnl AS REAL) > 0 THEN 1 ELSE 0 END) AS wins,
    AVG(t.realized_r)                             AS avg_r
FROM trades t
JOIN positions p ON p.id = t.position_id
GROUP BY trade_date, p.mode, p.run_id;

-- 날짜별 자산 변동 (미실현 포함)
CREATE VIEW v_daily_equity AS
SELECT
    date(ts_ms / 1000, 'unixepoch')   AS trade_date,
    mode,
    MIN(ts_ms)                        AS first_ms,
    MAX(ts_ms)                        AS last_ms,
    MAX(CAST(equity AS REAL))         AS equity_high,
    MIN(CAST(equity AS REAL))         AS equity_low
FROM account_snapshots
GROUP BY trade_date, mode;
```

> `CAST(... AS REAL)` 은 **집계와 화면 표시에만** 쓴다. 매매 판단 경로는 여전히 `Decimal` 이다 (§1).
> 두 뷰의 값은 포지션을 하루 넘겨 보유하면 달라진다. UI 는 둘 다 보여주고 차이를 설명한다.

---

## 5. 거래 재구성 뷰 (원 명세 §12의 요구사항)

```sql
CREATE VIEW v_trade_explain AS
SELECT
    t.id AS trade_id, t.opened_ms, t.side, t.net_pnl, t.realized_r, t.close_reason,
    d.direction, d.long_score, d.short_score, d.risk_score, d.confidence_score,
    d.expected_edge_bps, d.expected_cost_bps, d.required_edge_bps,
    d.components_json, d.data_quality_json,
    cv.hash AS config_hash, codev.git_sha,
    p.planned_risk_usdt, p.initial_stop_price,
    t.entry_fee, t.exit_fee, t.funding_paid,
    t.slippage_entry_bps, t.slippage_exit_bps, t.mae_bps, t.mfe_bps
FROM trades t
JOIN positions p        ON p.id = t.position_id
JOIN decisions d        ON d.id = p.entry_decision_id
JOIN config_versions cv ON cv.id = d.config_version_id
JOIN code_versions codev ON codev.id = d.code_version_id;
```

## 6. 보존 정책

| 테이블 | 보존 | 비고 |
|---|---|---|
| `candles` | 영구 | 압축된 parquet 아카이브 병행 |
| `decisions` | 라이브 영구 / 백테스트는 run 단위 삭제 | `components_json` 은 90일 후 NULL 처리 가능 (재계산 가능하므로). 단 **대시보드 거래상세 화면이 이를 쓰므로** 거래로 이어진 decision 은 보존 |
| `account_snapshots` | 1분 기록. 1년 후 1시간 단위로 다운샘플 | 자산 곡선 장기 보관 |
| `orders`, `fills`, `trades`, `positions` | 영구 | 회계 기록 |
| `news_items` | 180일 | 본문 저장 시 라이선스 확인 필요 |
| `errors`, `system_events` | 365일 | |
