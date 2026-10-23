-- 스트림(README 5-1)의 산출물 — Kafka 원문 토픽 · 스트림 봉. 층 목록에 'stream' 을 더한다.
ALTER TABLE run_artifacts DROP CONSTRAINT run_artifacts_layer_check;
ALTER TABLE run_artifacts
    ADD CONSTRAINT run_artifacts_layer_check CHECK (layer IN ('bronze', 'silver', 'gold', 'stream'));
