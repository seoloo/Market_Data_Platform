-- 선물 일반 주문을 가격 한도 지정가(LIMIT, IOC)로 바꾼다 (TRADING_CORE §주문 유형 — 시장가 금지, 2026-10-05).
--
-- OPEN·REDUCE·CLOSE 는 mark ± 한도 안의 지정가를 '즉시 체결 아니면 취소'(IOC)로 보낸다. 시장가는
-- 예외(보호 실패 비상 청산)에만 남는다. 지정가 가격을 기록하려고 limit_price 를 더한다.
--
-- SQLite 는 CHECK 제약을 바꿀 수 없어 테이블을 새로 만들고 행을 그대로 옮긴다(008 과 같은 방식).
CREATE TABLE futures_orders_new (
    client_order_id   TEXT    PRIMARY KEY,
    mode              TEXT    NOT NULL,
    strategy_id       TEXT    NOT NULL,
    decision_ms       INTEGER NOT NULL,
    symbol            TEXT    NOT NULL,
    side              TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type        TEXT    NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP_MARKET')),
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
    updated_ms        INTEGER NOT NULL,
    limit_price       TEXT    NOT NULL DEFAULT '0'
) WITHOUT ROWID;

INSERT INTO futures_orders_new (
    client_order_id, mode, strategy_id, decision_ms, symbol, side, order_type, purpose,
    reduce_only, close_position, qty, stop_price, state, exchange_order_id, filled_qty,
    avg_price, fee, fee_asset, error, created_ms, updated_ms
)
SELECT
    client_order_id, mode, strategy_id, decision_ms, symbol, side, order_type, purpose,
    reduce_only, close_position, qty, stop_price, state, exchange_order_id, filled_qty,
    avg_price, fee, fee_asset, error, created_ms, updated_ms
FROM futures_orders;

DROP TABLE futures_orders;

ALTER TABLE futures_orders_new RENAME TO futures_orders;

CREATE INDEX idx_futures_orders_open ON futures_orders (state)
    WHERE state IN ('PENDING', 'SUBMITTED', 'UNKNOWN', 'PARTIALLY_FILLED');
