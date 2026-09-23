-- 현물 주문 기록 (T/v2 Phase 5, docs/research/16 §5).
--
-- 선물용 intents/orders 는 decisions 에 묶여 있어 현물 일간 리밸런싱에 쓰지 않는다.
-- client_order_id 는 결정 시각·종목·방향으로 결정론적으로 만든다 → 재시작 후에도 같다.
-- PENDING 은 거래소 호출 **전에** 쓰는 선기록(write-ahead)이다. PENDING/UNKNOWN 이 남아 있으면
-- 거래소 조회로만 해소하고 추정 재주문하지 않는다.
CREATE TABLE spot_orders (
    client_order_id   TEXT    PRIMARY KEY,
    mode              TEXT    NOT NULL,
    strategy_id       TEXT    NOT NULL,
    decision_ms       INTEGER NOT NULL,
    symbol            TEXT    NOT NULL,
    side              TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty               TEXT    NOT NULL,
    target_weight     TEXT    NOT NULL,
    state             TEXT    NOT NULL CHECK (state IN (
                          'PENDING', 'SUBMITTED', 'UNKNOWN', 'PARTIALLY_FILLED',
                          'FILLED', 'EXPIRED', 'CANCELED', 'REJECTED', 'NOT_PLACED')),
    exchange_order_id TEXT,
    filled_qty        TEXT    NOT NULL DEFAULT '0',
    quote_qty         TEXT    NOT NULL DEFAULT '0',
    fee               TEXT    NOT NULL DEFAULT '0',
    fee_asset         TEXT,
    error             TEXT,
    created_ms        INTEGER NOT NULL,
    updated_ms        INTEGER NOT NULL
) WITHOUT ROWID;

CREATE INDEX idx_spot_orders_unresolved ON spot_orders (state)
    WHERE state IN ('PENDING', 'SUBMITTED', 'UNKNOWN', 'PARTIALLY_FILLED');
