-- O/v1 Paper 가상 원장 (docs/research/25 전향 관찰).
--
-- 하루 한 행. T/v2 Paper(paper_days)와 테이블·원장을 분리한다 — 두 관찰이 서로의 자본을 건드리지 않는다.
-- T/v2 는 체결 기록에서 잔고를 다시 계산하지만, O/v1 은 선물 지갑·고점·정지 상태·funding 이 있어
-- 하루의 **전체 상태**를 행에 저장한다. 한 실행이 쓰는 행들은 한 트랜잭션이라, 중간에 죽으면
-- 아무것도 남지 않고 다음 실행이 같은 날을 처음부터 다시 계산한다.
--
-- 수치는 JSON 숫자(부동소수의 repr)로 저장한다. 연구 시뮬레이터(backtest/overlay.py)와
-- 비트 단위로 같은 경로를 재현하기 위해서다 (Phase 6 parity). 실거래 회계는 Decimal 을 쓴다.
CREATE TABLE paper_overlay_days (
    strategy_id    TEXT    NOT NULL,
    decision_ms    INTEGER NOT NULL,   -- 마감 일봉의 close_ms (00:00 UTC − 1ms)
    status         TEXT    NOT NULL,   -- OK | NO_PRIOR_DECISION | MISSED
    equity         TEXT    NOT NULL,   -- 그날 종가 기준 가상 자본 (USDT)
    state_json     TEXT    NOT NULL,   -- OverlayState (다음 날로 넘어가는 전부, 내일 체결할 목표 포함)
    tally_json     TEXT    NOT NULL,   -- 그날 손익·funding·비용·청산·정지·일 손실 발동
    market_json    TEXT    NOT NULL,   -- 그날 계산에 쓴 시장 입력 (재현·감사용)
    fills_json     TEXT    NOT NULL,   -- 그날 바뀐 포지션
    observed_json  TEXT    NOT NULL,   -- 실행 시각의 현재가 (체결가와의 차이 관찰용, 계산에는 안 씀)
    note           TEXT,
    created_ms     INTEGER NOT NULL,
    PRIMARY KEY (strategy_id, decision_ms)
) WITHOUT ROWID;
