-- USD-M 선물 주문 기록 (Phase 5 선물, execution/futures_orders.py).
--
-- 현물(spot_orders)과 같은 규약: client_order_id 는 전략·결정 시각·종목·목적·방향(+재발행 번호)으로
-- 결정론적으로 만든다. PENDING 은 거래소 호출 **전에** 쓰는 선기록이다. PENDING/UNKNOWN 은 거래소
-- 조회로만 해소하고 추정 재주문하지 않는다.
--
-- 선물 고유: 보호 스톱(STOP_MARKET, closePosition)은 거래소에 걸려 있는 동안 SUBMITTED 로 남는다.
-- 이것은 정상 상태이며 신규 주문을 막지 않는다 — 막는 것은 결과를 모르는 주문(PENDING/UNKNOWN)과
-- 체결 중인 시장가 주문뿐이다.
CREATE TABLE futures_orders (
    client_order_id   TEXT    PRIMARY KEY,
    mode              TEXT    NOT NULL,
    strategy_id       TEXT    NOT NULL,
    decision_ms       INTEGER NOT NULL,
    symbol            TEXT    NOT NULL,
    side              TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type        TEXT    NOT NULL CHECK (order_type IN ('MARKET', 'STOP_MARKET')),
    purpose           TEXT    NOT NULL CHECK (purpose IN ('OPEN', 'REDUCE', 'CLOSE', 'PROTECT')),
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

CREATE INDEX idx_futures_orders_open ON futures_orders (state)
    WHERE state IN ('PENDING', 'SUBMITTED', 'UNKNOWN', 'PARTIALLY_FILLED');
