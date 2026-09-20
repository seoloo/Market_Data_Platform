-- 001_initial.sql — 최초 스키마
--
-- 설계: docs/phase0/03-data-model.md
--
-- 규칙:
--   * 모든 시각은 UTC epoch milliseconds (INTEGER), 컬럼명에 _ms 접미사
--   * 금액·수량은 TEXT (Decimal 문자열). REAL 금지 — 정밀도 오류가 주문 거부를 만든다
--   * 점수·비율처럼 표시/집계 전용 값만 REAL 을 쓴다
--
-- 이 파일은 적용 후 **수정하지 않는다**. 체크섬이 기록되어 있어 변경하면 기동이 막힌다.
-- 스키마를 바꾸려면 002_*.sql 을 추가한다.

-- ============================================================ 메타 / 버전
--
-- schema_version 과 schema_migrations 는 마이그레이션 러너(storage/migrations.py)가
-- 소유한다. 마이그레이션 자체가 실행되기 전에 존재해야 하므로 여기서 만들지 않는다.

-- 재현성 고정점 1: 설정
CREATE TABLE config_versions (
    id              INTEGER PRIMARY KEY,
    hash            TEXT    NOT NULL UNIQUE,
    config_json     TEXT    NOT NULL,
    created_ms      INTEGER NOT NULL,
    note            TEXT
);

-- 재현성 고정점 2: 코드
CREATE TABLE code_versions (
    id              INTEGER PRIMARY KEY,
    git_sha         TEXT    NOT NULL,
    dirty           INTEGER NOT NULL CHECK (dirty IN (0, 1)),
    deps_json       TEXT    NOT NULL,
    created_ms      INTEGER NOT NULL,
    UNIQUE (git_sha, dirty, deps_json)
);

-- 거래소 심볼 규격. 매 기동 시 exchangeInfo 로 갱신한다
CREATE TABLE instruments (
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    contract_type   TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    tick_size       TEXT    NOT NULL,
    step_size       TEXT    NOT NULL,
    min_qty         TEXT    NOT NULL,
    min_notional    TEXT    NOT NULL,
    max_leverage    INTEGER NOT NULL,
    fetched_ms      INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol)
);

-- ============================================================ 시장 데이터

CREATE TABLE candles (
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL,
    open_ms         INTEGER NOT NULL,
    close_ms        INTEGER NOT NULL,
    open            TEXT    NOT NULL,
    high            TEXT    NOT NULL,
    low             TEXT    NOT NULL,
    close           TEXT    NOT NULL,
    volume          TEXT    NOT NULL,
    quote_volume    TEXT    NOT NULL DEFAULT '0',
    trade_count     INTEGER NOT NULL DEFAULT 0,
    taker_buy_base  TEXT,
    source          TEXT    NOT NULL CHECK (source IN ('ws', 'rest', 'bulk', 'derived')),
    ingested_ms     INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol, timeframe, open_ms)
) WITHOUT ROWID;

CREATE INDEX idx_candles_lookup ON candles (symbol, timeframe, open_ms DESC);

-- 무결성 검사 결과. 갭은 "설명 가능해야" 한다 (거래소 점검 등)
CREATE TABLE candle_gaps (
    id              INTEGER PRIMARY KEY,
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL,
    missing_from_ms INTEGER NOT NULL,
    missing_to_ms   INTEGER NOT NULL,
    bar_count       INTEGER NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    detected_ms     INTEGER NOT NULL,
    resolved_ms     INTEGER,
    note            TEXT,
    UNIQUE (exchange, symbol, timeframe, missing_from_ms)
);

CREATE INDEX idx_gaps_open ON candle_gaps (symbol, timeframe, resolved);

CREATE TABLE funding_rates (
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    funding_ms      INTEGER NOT NULL,
    rate            TEXT    NOT NULL,
    mark_price      TEXT,
    PRIMARY KEY (exchange, symbol, funding_ms)
) WITHOUT ROWID;

-- 수수료율은 하드코딩하지 않는다 (원 명세 §6)
CREATE TABLE fee_schedule (
    exchange        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    maker_rate      TEXT    NOT NULL,
    taker_rate      TEXT    NOT NULL,
    effective_ms    INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol, effective_ms)
);

-- ============================================================ 판단 기록

CREATE TABLE decisions (
    id                  INTEGER PRIMARY KEY,
    as_of_ms            INTEGER NOT NULL,
    created_ms          INTEGER NOT NULL,
    symbol              TEXT    NOT NULL,
    mode                TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    config_version_id   INTEGER NOT NULL REFERENCES config_versions (id),
    code_version_id     INTEGER NOT NULL REFERENCES code_versions (id),
    snapshot_digest     TEXT    NOT NULL,
    decision_digest     TEXT    NOT NULL,

    direction           REAL    NOT NULL,
    long_score          REAL    NOT NULL,
    short_score         REAL    NOT NULL,
    confidence_score    REAL    NOT NULL,
    risk_score          REAL    NOT NULL,
    bias_direction      INTEGER NOT NULL,
    bias_multiplier     REAL    NOT NULL,
    agreement           REAL    NOT NULL,
    regime              TEXT,

    expected_edge_bps   REAL,
    expected_cost_bps   REAL,
    required_edge_bps   REAL,

    action              TEXT    NOT NULL,
    veto_reasons        TEXT,
    intent_json         TEXT,
    components_json     TEXT,
    data_quality_json   TEXT,
    sizing_json         TEXT,
    latency_ms          INTEGER,

    UNIQUE (run_id, symbol, as_of_ms)
);

CREATE INDEX idx_decisions_time ON decisions (symbol, as_of_ms DESC);
CREATE INDEX idx_decisions_action ON decisions (action, as_of_ms DESC);
CREATE INDEX idx_decisions_run ON decisions (run_id, as_of_ms);

-- ============================================================ 주문 / 포지션

CREATE TABLE positions (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT    NOT NULL,
    mode                TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    side                TEXT    NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    state               TEXT    NOT NULL,
    opened_ms           INTEGER,
    closed_ms           INTEGER,
    entry_decision_id   INTEGER REFERENCES decisions (id),
    exit_decision_id    INTEGER REFERENCES decisions (id),
    qty                 TEXT    NOT NULL DEFAULT '0',
    entry_avg_price     TEXT,
    exit_avg_price      TEXT,
    exchange_leverage   INTEGER,
    effective_leverage  REAL,
    margin_allocated    TEXT,
    initial_stop_price  TEXT,
    current_stop_price  TEXT,
    take_profit_price   TEXT,
    liquidation_price   TEXT,
    planned_risk        TEXT,
    close_reason        TEXT
);

CREATE INDEX idx_positions_state ON positions (mode, state);
CREATE INDEX idx_positions_opened ON positions (symbol, opened_ms DESC);

CREATE TABLE intents (
    id              INTEGER PRIMARY KEY,
    decision_id     INTEGER NOT NULL REFERENCES decisions (id),
    created_ms      INTEGER NOT NULL,
    symbol          TEXT    NOT NULL,
    purpose         TEXT    NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type      TEXT    NOT NULL,
    qty             TEXT    NOT NULL,
    price           TEXT,
    stop_price      TEXT,
    reduce_only     INTEGER NOT NULL DEFAULT 0 CHECK (reduce_only IN (0, 1)),
    close_position  INTEGER NOT NULL DEFAULT 0 CHECK (close_position IN (0, 1)),
    time_in_force   TEXT,
    client_order_id TEXT    NOT NULL UNIQUE,
    state           TEXT    NOT NULL
);

CREATE TABLE orders (
    id                  INTEGER PRIMARY KEY,
    intent_id           INTEGER REFERENCES intents (id),
    position_id         INTEGER REFERENCES positions (id),
    client_order_id     TEXT    NOT NULL UNIQUE,
    exchange_order_id   TEXT,
    symbol              TEXT    NOT NULL,
    mode                TEXT    NOT NULL,
    side                TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type          TEXT    NOT NULL,
    purpose             TEXT    NOT NULL,
    qty                 TEXT    NOT NULL,
    price               TEXT,
    stop_price          TEXT,
    reduce_only         INTEGER NOT NULL DEFAULT 0 CHECK (reduce_only IN (0, 1)),
    close_position      INTEGER NOT NULL DEFAULT 0 CHECK (close_position IN (0, 1)),
    state               TEXT    NOT NULL,
    filled_qty          TEXT    NOT NULL DEFAULT '0',
    avg_fill_price      TEXT,
    fee_paid            TEXT    NOT NULL DEFAULT '0',
    fee_asset           TEXT,
    submitted_ms        INTEGER,
    last_update_ms      INTEGER NOT NULL,
    closed_ms           INTEGER,
    reject_reason       TEXT,
    raw_last_json       TEXT
);

-- UNKNOWN 주문을 빠르게 찾기 위한 인덱스. 하나라도 있으면 신규 진입이 금지된다
CREATE INDEX idx_orders_state ON orders (mode, state, last_update_ms);

CREATE TABLE fills (
    id                  INTEGER PRIMARY KEY,
    order_id            INTEGER NOT NULL REFERENCES orders (id),
    exchange_trade_id   TEXT    NOT NULL UNIQUE,   -- 중복 이벤트 방어
    ts_ms               INTEGER NOT NULL,
    qty                 TEXT    NOT NULL,
    price               TEXT    NOT NULL,
    fee                 TEXT    NOT NULL,
    fee_asset           TEXT    NOT NULL,
    is_maker            INTEGER NOT NULL CHECK (is_maker IN (0, 1)),
    realized_pnl        TEXT
);

CREATE INDEX idx_fills_order ON fills (order_id, ts_ms);

-- 왕복 결과. realized_r 이 사이징과 무관하게 전략을 평가하는 유일한 척도다 (D9)
CREATE TABLE trades (
    id                  INTEGER PRIMARY KEY,
    position_id         INTEGER NOT NULL UNIQUE REFERENCES positions (id),
    symbol              TEXT    NOT NULL,
    mode                TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    side                TEXT    NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    opened_ms           INTEGER NOT NULL,
    closed_ms           INTEGER NOT NULL,
    holding_ms          INTEGER NOT NULL,
    qty                 TEXT    NOT NULL,
    entry_price         TEXT    NOT NULL,
    exit_price          TEXT    NOT NULL,
    gross_pnl           TEXT    NOT NULL,
    entry_fee           TEXT    NOT NULL DEFAULT '0',
    exit_fee            TEXT    NOT NULL DEFAULT '0',
    funding_paid        TEXT    NOT NULL DEFAULT '0',
    net_pnl             TEXT    NOT NULL,
    planned_risk        TEXT,
    realized_r          REAL,
    slippage_entry_bps  REAL,
    slippage_exit_bps   REAL,
    mae_bps             REAL,
    mfe_bps             REAL,
    close_reason        TEXT    NOT NULL
);

CREATE INDEX idx_trades_closed ON trades (mode, run_id, closed_ms);

-- ============================================================ 계좌 / 리스크

-- 1분 주기 기록 (D10). 자산 곡선 해상도를 위해 Phase 1 부터 기록한다
CREATE TABLE account_snapshots (
    id                  INTEGER PRIMARY KEY,
    ts_ms               INTEGER NOT NULL,
    mode                TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    wallet_balance      TEXT    NOT NULL,
    unrealized_pnl      TEXT    NOT NULL DEFAULT '0',
    equity              TEXT    NOT NULL,
    available_balance   TEXT    NOT NULL,
    equity_high_water   TEXT    NOT NULL,   -- 영속화 필수. 재시작 시 DD 리셋 방지
    source              TEXT    NOT NULL CHECK (source IN ('exchange', 'computed')),
    UNIQUE (mode, run_id, ts_ms)
);

CREATE INDEX idx_acct_ts ON account_snapshots (mode, ts_ms DESC);

CREATE TABLE risk_state (
    id                  INTEGER PRIMARY KEY,
    ts_ms               INTEGER NOT NULL,
    mode                TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    trading_day         TEXT    NOT NULL,   -- 'YYYY-MM-DD' UTC
    day_realized_pnl    TEXT    NOT NULL DEFAULT '0',
    day_trade_count     INTEGER NOT NULL DEFAULT 0,
    consecutive_losses  INTEGER NOT NULL DEFAULT 0,
    drawdown_pct        REAL    NOT NULL DEFAULT 0,
    halted              INTEGER NOT NULL DEFAULT 0 CHECK (halted IN (0, 1)),
    halt_reason         TEXT,
    cooldown_until_ms   INTEGER
);

CREATE INDEX idx_risk_state_ts ON risk_state (mode, ts_ms DESC);

CREATE TABLE risk_events (
    id              INTEGER PRIMARY KEY,
    ts_ms           INTEGER NOT NULL,
    mode            TEXT    NOT NULL,
    severity        TEXT    NOT NULL CHECK (severity IN ('info', 'warn', 'critical')),
    kind            TEXT    NOT NULL,
    decision_id     INTEGER REFERENCES decisions (id),
    detail_json     TEXT    NOT NULL,
    action_taken    TEXT
);

CREATE INDEX idx_risk_events_ts ON risk_events (ts_ms DESC);

-- ============================================================ 거시 / 뉴스 (Phase 10+)

-- vintage_ms 가 핵심이다. 백테스트는 WHERE vintage_ms <= as_of_ms 로 조회한다.
-- 수정된 최신값을 쓰면 look-ahead 가 된다 (03 §3.6)
CREATE TABLE macro_events (
    id                  INTEGER PRIMARY KEY,
    indicator           TEXT    NOT NULL,
    scheduled_ms        INTEGER NOT NULL,
    released_ms         INTEGER,
    period              TEXT,
    actual              TEXT,
    consensus           TEXT,
    previous            TEXT,
    revised_previous    TEXT,
    surprise            TEXT,
    surprise_z          REAL,
    source              TEXT    NOT NULL,
    vintage_ms          INTEGER NOT NULL,
    UNIQUE (indicator, period, vintage_ms)
);

CREATE INDEX idx_macro_schedule ON macro_events (scheduled_ms);

CREATE TABLE news_items (
    id                  INTEGER PRIMARY KEY,
    external_id         TEXT UNIQUE,
    source              TEXT    NOT NULL,
    url                 TEXT,
    title               TEXT    NOT NULL,
    summary             TEXT,
    published_ms        INTEGER NOT NULL,
    ingested_ms         INTEGER NOT NULL,
    content_hash        TEXT    NOT NULL,
    simhash             TEXT,
    cluster_id          TEXT,
    prefilter_score     REAL,
    selected_for_llm    INTEGER NOT NULL DEFAULT 0 CHECK (selected_for_llm IN (0, 1))
);

CREATE INDEX idx_news_published ON news_items (published_ms DESC);

CREATE TABLE news_analyses (
    id                      INTEGER PRIMARY KEY,
    news_id                 INTEGER NOT NULL REFERENCES news_items (id),
    model                   TEXT    NOT NULL,   -- 모델이 바뀌면 다른 전략이다
    prompt_version          TEXT    NOT NULL,
    analyzed_ms             INTEGER NOT NULL,
    relevance               REAL,
    importance              REAL,
    bullish_score           REAL,
    bearish_score           REAL,
    confidence              REAL,
    expected_duration_min   INTEGER,
    event_type              TEXT,
    duplicate_event_id      INTEGER,
    reason                  TEXT,
    raw_response_json       TEXT    NOT NULL,
    input_digest            TEXT    NOT NULL,
    UNIQUE (news_id, model, prompt_version)
);

-- ============================================================ 운영

CREATE TABLE system_events (
    id          INTEGER PRIMARY KEY,
    ts_ms       INTEGER NOT NULL,
    mode        TEXT,
    kind        TEXT    NOT NULL,
    detail_json TEXT
);

CREATE INDEX idx_system_events_ts ON system_events (ts_ms DESC);

CREATE TABLE errors (
    id              INTEGER PRIMARY KEY,
    ts_ms           INTEGER NOT NULL,
    mode            TEXT,
    component       TEXT    NOT NULL,
    error_type      TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    traceback       TEXT,
    context_json    TEXT,
    handled         INTEGER NOT NULL DEFAULT 1 CHECK (handled IN (0, 1))
);

CREATE INDEX idx_errors_ts ON errors (ts_ms DESC);

-- 워치독이 읽는다
CREATE TABLE heartbeats (
    component   TEXT    PRIMARY KEY,
    ts_ms       INTEGER NOT NULL,
    state       TEXT    NOT NULL CHECK (state IN ('RUNNING', 'DEGRADED', 'HALTED')),
    detail      TEXT
) WITHOUT ROWID;

CREATE TABLE backtest_runs (
    run_id              TEXT    PRIMARY KEY,
    created_ms          INTEGER NOT NULL,
    config_version_id   INTEGER NOT NULL REFERENCES config_versions (id),
    code_version_id     INTEGER NOT NULL REFERENCES code_versions (id),
    data_from_ms        INTEGER NOT NULL,
    data_to_ms          INTEGER NOT NULL,
    split               TEXT    NOT NULL CHECK (
                            split IN ('train', 'validation', 'test', 'walkforward')
                        ),
    metrics_json        TEXT,
    notes               TEXT
) WITHOUT ROWID;

-- ============================================================ 집계 뷰 (D10)

-- 날짜별 실현손익 (달력 화면). UTC 기준 = Risk Engine 일일 리셋과 동일
CREATE VIEW v_daily_pnl AS
SELECT
    date(t.closed_ms / 1000, 'unixepoch')                       AS trade_date,
    t.mode,
    t.run_id,
    COUNT(*)                                                    AS trade_count,
    SUM(CAST(t.net_pnl AS REAL))                                AS realized_pnl,
    SUM(CAST(t.gross_pnl AS REAL))                              AS gross_pnl,
    SUM(CAST(t.entry_fee AS REAL) + CAST(t.exit_fee AS REAL))   AS fees,
    SUM(CAST(t.funding_paid AS REAL))                           AS funding,
    SUM(CASE WHEN CAST(t.net_pnl AS REAL) > 0 THEN 1 ELSE 0 END) AS wins,
    AVG(t.realized_r)                                           AS avg_r
FROM trades t
GROUP BY trade_date, t.mode, t.run_id;

-- 날짜별 자산 변동 (미실현 포함)
CREATE VIEW v_daily_equity AS
SELECT
    date(ts_ms / 1000, 'unixepoch')     AS trade_date,
    mode,
    run_id,
    MIN(ts_ms)                          AS first_ms,
    MAX(ts_ms)                          AS last_ms,
    MAX(CAST(equity AS REAL))           AS equity_high,
    MIN(CAST(equity AS REAL))           AS equity_low,
    COUNT(*)                            AS sample_count
FROM account_snapshots
GROUP BY trade_date, mode, run_id;

-- 거래 재구성 (원 명세 §12). 대시보드 "거래 상세" 화면의 원천
CREATE VIEW v_trade_explain AS
SELECT
    t.id                AS trade_id,
    t.opened_ms,
    t.closed_ms,
    t.side,
    t.net_pnl,
    t.realized_r,
    t.close_reason,
    t.entry_fee,
    t.exit_fee,
    t.funding_paid,
    t.slippage_entry_bps,
    t.slippage_exit_bps,
    t.mae_bps,
    t.mfe_bps,
    d.direction,
    d.long_score,
    d.short_score,
    d.risk_score,
    d.confidence_score,
    d.regime,
    d.expected_edge_bps,
    d.expected_cost_bps,
    d.required_edge_bps,
    d.components_json,
    d.data_quality_json,
    d.sizing_json,
    p.planned_risk,
    p.initial_stop_price,
    p.exchange_leverage,
    p.effective_leverage,
    p.liquidation_price,
    cv.hash             AS config_hash,
    codev.git_sha       AS git_sha,
    codev.dirty         AS code_dirty
FROM trades t
JOIN positions p         ON p.id = t.position_id
LEFT JOIN decisions d    ON d.id = p.entry_decision_id
LEFT JOIN config_versions cv ON cv.id = d.config_version_id
LEFT JOIN code_versions codev ON codev.id = d.code_version_id;
