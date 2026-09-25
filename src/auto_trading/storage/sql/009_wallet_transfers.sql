-- 현물 ↔ 선물 지갑 이체 기록 (docs/research/53, execution/wallet_transfers.py).
--
-- Binance 이체 API 에는 클라이언트 멱등 ID 가 없다. 그래서 주문과 같은 규약으로 **거래소 호출 전에**
-- PENDING 을 쓰고(선기록), 응답을 잃으면 UNKNOWN 으로 두고 이력 조회로만 해소한다. 추정 재이체 금지.
-- intent_id 는 전략·결정 시각·방향·재발행 번호로 정해진다 — 같은 의도는 몇 번을 호출해도 한 건.
CREATE TABLE wallet_transfers (
    intent_id    TEXT    PRIMARY KEY,
    mode         TEXT    NOT NULL,
    strategy_id  TEXT    NOT NULL,
    decision_ms  INTEGER NOT NULL,
    direction    TEXT    NOT NULL CHECK (direction IN ('MAIN_UMFUTURE', 'UMFUTURE_MAIN')),
    asset        TEXT    NOT NULL,
    amount       TEXT    NOT NULL,
    state        TEXT    NOT NULL CHECK (state IN (
                     'PENDING', 'UNKNOWN', 'CONFIRMED', 'REJECTED', 'NOT_FOUND')),
    tran_id      TEXT,
    error        TEXT,
    created_ms   INTEGER NOT NULL,
    updated_ms   INTEGER NOT NULL
) WITHOUT ROWID;

CREATE INDEX idx_wallet_transfers_open ON wallet_transfers (state)
    WHERE state IN ('PENDING', 'UNKNOWN');
