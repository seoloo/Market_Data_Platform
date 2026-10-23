-- 스트림 · 배치 매일 대사 (README 5-2). mdp.reconcile.daily 가 대사 한 번마다 **더하기만** 한다
-- (지우거나 고치지 않는다) — 메우기 전에 찾은 원인과 메운 뒤의 상태가 둘 다 남는다.
-- 그날의 마지막 결과는 뷰 reconcile_daily_latest.

-- 대사 한 번(종목 · 하루) = 한 줄: 몇 분이 같았고, 다른 분은 원인별로 몇 분인가.
CREATE TABLE reconcile_daily (
    run_id               uuid         PRIMARY KEY REFERENCES pipeline_runs (run_id),
    symbol               text         NOT NULL,
    trade_date           date         NOT NULL,
    reconciled_at        timestamptz  NOT NULL,
    batch_minutes        integer      NOT NULL,   -- 배치 봉이 있는 분
    stream_minutes       integer      NOT NULL,   -- 스트림 표에 봉이 있는 분 (메운 분 포함)
    matched              integer      NOT NULL,   -- 스트림이 만든 봉이 배치와 칸마다 같음
    repaired             integer      NOT NULL,   -- 이미 배치 봉으로 바꿔 넣은 분 (같지만 스트림 몫은 아님)
    stream_gap           integer      NOT NULL,   -- 원인: 받지 못한 체결이 있음 (끊김 · PC 잠듦)
    late                 integer      NOT NULL,   -- 원인: 받았지만 워터마크 뒤라 버림
    duplicate            integer      NOT NULL,   -- 원인: 스트림이 체결을 더 셈
    other                integer      NOT NULL,   -- 원인 미상 — 사람이 확인
    only_stream          integer      NOT NULL,   -- 배치에 없는 분 (배치가 덜 만들어짐?)
    match_rate           numeric(9, 6) NOT NULL,  -- matched / batch_minutes
    trades_not_received  bigint       NOT NULL,   -- 배치에는 있는데 원문 토픽에 없는 묶음 수
    trades_late          bigint       NOT NULL,   -- 원문 토픽에는 있는데 봉에 안 들어간 묶음 수
    repaired_now         integer      NOT NULL,   -- 이 대사가 끝에 배치 봉으로 바꿔 넣은 분 (--repair)
    stream_source        text,                    -- 원문 토픽의 출처 (replay · live · 섞임)
    ingest_lag_p50_ms    bigint,                  -- 받은 시각 − 체결 시각 (live 일 때만)
    ingest_lag_p95_ms    bigint,
    ingest_lag_max_ms    bigint,
    batch_version        bigint       NOT NULL,   -- 맞댄 Delta 표 버전 (계보)
    stream_version       bigint       NOT NULL,
    CHECK (matched + repaired + stream_gap + late + duplicate + other = batch_minutes)
);
CREATE INDEX reconcile_daily_day ON reconcile_daily (symbol, trade_date, reconciled_at DESC);

-- 다른 분 = 한 줄 (같은 분 · 메운 분은 남기지 않는다 — 하루 1,440 줄 중 대부분이 같으므로).
CREATE TABLE reconcile_minutes (
    run_id             uuid    NOT NULL REFERENCES reconcile_daily (run_id),
    symbol             text    NOT NULL,
    bar_start_ms       bigint  NOT NULL,
    cause              text    NOT NULL
        CHECK (cause IN ('stream_gap', 'late', 'duplicate', 'other', 'only_stream')),
    batch_agg_trades   bigint,                     -- 배치 봉의 묶음 수 (없으면 NULL)
    stream_agg_trades  bigint,                     -- 스트림 봉의 묶음 수 (없으면 NULL)
    not_received       bigint  NOT NULL,           -- 그 분에서 원문 토픽에 없는 묶음
    late_trades        bigint  NOT NULL,           -- 그 분에서 받았지만 빠진 묶음
    differing_columns  text[]  NOT NULL,           -- 다른 값 칸 (봉이 한쪽에만 있으면 빈 목록)
    PRIMARY KEY (run_id, bar_start_ms)
);

CREATE VIEW reconcile_daily_latest AS
SELECT DISTINCT ON (symbol, trade_date) *
FROM reconcile_daily
ORDER BY symbol, trade_date, reconciled_at DESC;
