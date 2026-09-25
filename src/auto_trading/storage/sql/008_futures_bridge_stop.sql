-- 선물 주문 목적에 BRIDGE(다리 스톱)를 더한다 (docs/research/52 §4.1, O/v2 안전성 검증 V2).
--
-- 매일 보호 스톱 가격을 바꿀 때 같은 방향 closePosition 스톱은 하나만 걸린다(-4130).
-- 그래서 새 가격에 **수량 지정 reduce-only 스톱(다리)**을 먼저 걸고, 옛 스톱 취소 → 새 closePosition →
-- 다리 취소 순서로 교체한다. 다리 스톱도 거래소에 걸린 보호 주문이라 같은 테이블에 남긴다.
--
-- SQLite 는 CHECK 제약을 바꿀 수 없어 테이블을 새로 만들고 행을 그대로 옮긴다.
CREATE TABLE futures_orders_new (
    client_order_id   TEXT    PRIMARY KEY,
    mode              TEXT    NOT NULL,
    strategy_id       TEXT    NOT NULL,
    decision_ms       INTEGER NOT NULL,
    symbol            TEXT    NOT NULL,
    side              TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type        TEXT    NOT NULL CHECK (order_type IN ('MARKET', 'STOP_MARKET')),
    purpose           TEXT    NOT NULL CHECK (purpose IN ('OPEN', 'REDUCE', 'CLOSE', 'PROTECT', 'BRIDGE')),
    reduce_only       INTEGER NOT NULL CHECK (reduce_only IN (0, 1)),
    close_position    INTEGER NOT NULL CHECK (close_position IN (0, 1)),
    qty               TEXT    NOT NULL,
    stop_price        TEXT    NOT NULL DEFAULT '0',
    state             TEXT    NOT NULL CHECK (state IN (
                          'PENDING', 'SUBMITTED', 'UNKNOWN', 'PARTIALLY_FILLED',
                          'FILLED', 'EXPIRED', 'CANCELED', 'REJECTED', 'NOT_PLACED')),
    exchange_order_id TEXT,
    filled_qty        TEXT    NOT NULL DEFAULT '0',
    avg_price         TEXT    NOT NULL DEFAULT '0',
    fee               TEXT    NOT NULL DEFAULT '0',
    fee_asset         TEXT,
    error             TEXT,
    created_ms        INTEGER NOT NULL,
    updated_ms        INTEGER NOT NULL
) WITHOUT ROWID;

INSERT INTO futures_orders_new SELECT * FROM futures_orders;

DROP TABLE futures_orders;

ALTER TABLE futures_orders_new RENAME TO futures_orders;

CREATE INDEX idx_futures_orders_open ON futures_orders (state)
    WHERE state IN ('PENDING', 'SUBMITTED', 'UNKNOWN', 'PARTIALLY_FILLED');
