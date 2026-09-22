-- 무기한 선물 포지셔닝 지표 (H2 / 16-h2-proposal.md).
--
-- 가격에서 파생되지 않는 유일한 정보원이다. H1 이 실패한 이유가 5개 feature 가
-- 전부 가격 파생값이었기 때문이므로 (15-h1-postmortem.md §6), 이 테이블이
-- 다음 가설의 근거가 된다.
--
-- Binance 는 5분 간격 스냅샷으로 제공한다. at_ms 시점의 상태이며 그 시각에
-- 알 수 있으므로, 조회는 candles 와 같은 at_ms <= as_of_ms 규칙을 쓴다.
CREATE TABLE perp_metrics (
    exchange                 TEXT    NOT NULL,
    symbol                   TEXT    NOT NULL,
    at_ms                    INTEGER NOT NULL,
    open_interest            TEXT    NOT NULL,  -- 계약 수량 (BTC)
    open_interest_value      TEXT    NOT NULL,  -- 명목가 (USDT)
    toptrader_account_ratio  TEXT    NOT NULL,  -- 상위 트레이더 계좌 수 롱/숏
    toptrader_position_ratio TEXT    NOT NULL,  -- 상위 트레이더 명목가 롱/숏
    account_long_short_ratio TEXT    NOT NULL,  -- 전체 계좌 롱/숏 (군중)
    taker_buy_sell_ratio     TEXT    NOT NULL,  -- 테이커 매수/매도 거래량 비
    PRIMARY KEY (exchange, symbol, at_ms)
) WITHOUT ROWID;

CREATE INDEX idx_perp_metrics_lookup ON perp_metrics (exchange, symbol, at_ms DESC);
