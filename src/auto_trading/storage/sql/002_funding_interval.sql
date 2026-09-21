-- funding 주기를 저장한다.
--
-- Binance 는 심볼마다 8h / 4h / 1h 로 주기가 다르고, 같은 심볼도 시점에 따라 바뀐다.
-- 다음 정산 시각(next_funding_ms)을 계산하려면 그 시점의 주기를 알아야 하는데,
-- 연속한 두 행의 간격으로 역산하면 결측이 있을 때 조용히 틀린다.
-- 아카이브가 값을 직접 주므로 그대로 저장한다.
--
-- 기존 행은 없다(테이블이 비어 있음). 기본값 8 은 BTCUSDT 기준이며,
-- 적재 시에는 항상 아카이브 값을 명시적으로 넣는다.
ALTER TABLE funding_rates ADD COLUMN interval_hours INTEGER NOT NULL DEFAULT 8;

CREATE INDEX idx_funding_lookup ON funding_rates (exchange, symbol, funding_ms DESC);
