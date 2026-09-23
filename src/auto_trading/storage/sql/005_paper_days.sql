-- Paper 일일 결정·체결 기록 (T/v2 Phase 6, docs/research/16 §5).
--
-- 하루 한 행. 가상 잔고는 따로 저장하지 않고 spot_orders(mode='paper')의 체결 기록에서 계산한다 —
-- 프로세스가 어디서 죽어도 잔고와 주문 기록이 어긋나지 않게 하기 위해서다.
-- targets_json 은 이 날 종가로 정한 목표이며, **다음 날** 실행이 체결한다 (연구의 1일 지연과 같다).
CREATE TABLE paper_days (
    strategy_id    TEXT    NOT NULL,
    decision_ms    INTEGER NOT NULL,   -- 마감 일봉의 close_ms (00:00 UTC − 1ms)
    status         TEXT    NOT NULL,   -- OK | DEGRADED | NO_PRIOR_DECISION
    equity         TEXT    NOT NULL,   -- 실행 직후 가상 자본 (USDT)
    holdings_json  TEXT    NOT NULL,
    prices_json    TEXT    NOT NULL,
    targets_json   TEXT    NOT NULL,   -- {symbol: weight | null}
    executed_json  TEXT    NOT NULL,   -- 이번 실행에서 체결한 주문 요약
    note           TEXT,
    created_ms     INTEGER NOT NULL,
    PRIMARY KEY (strategy_id, decision_ms)
) WITHOUT ROWID;
