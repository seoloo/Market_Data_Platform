-- 조회용 Gold 1 분봉 (README 4-4). 원본은 Delta 표 gold/bars_1m 하나이고, 이것은 그 복사본이다.
-- mdp.serve.bars_1m 이 달 단위로 지우고 다시 넣는다(멱등) — 직접 고치지 않는다.
CREATE TABLE gold_bars_1m (
    symbol                 text           NOT NULL,
    bar_start_ms           bigint         NOT NULL,          -- 분 시작 (UTC ms)
    bar_start              timestamptz    NOT NULL,
    open                   numeric(18, 8) NOT NULL,
    high                   numeric(18, 8) NOT NULL,
    low                    numeric(18, 8) NOT NULL,
    close                  numeric(18, 8) NOT NULL,
    volume                 numeric(28, 8) NOT NULL,
    quote_volume           numeric(38, 16) NOT NULL,
    taker_buy_volume       numeric(28, 8) NOT NULL,
    taker_buy_quote_volume numeric(38, 16) NOT NULL,
    trade_count            bigint         NOT NULL,          -- 개별 체결 수 (공식 봉 count 와 같은 뜻)
    agg_trade_count        bigint         NOT NULL,          -- 묶음(aggTrade) 수
    gold_version           bigint         NOT NULL,          -- 복사한 Delta 표 버전 (계보)
    loaded_run_id          uuid           NOT NULL,          -- 복사한 작업 (pipeline_runs.run_id)
    PRIMARY KEY (symbol, bar_start_ms),
    CHECK (low <= open AND low <= close AND high >= open AND high >= close)
);
