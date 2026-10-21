-- 실행 기록 표 (README 3-2). 이미 적용된 이 파일은 고치지 않는다 — 바꿀 것은 002_ 로 새로 쓴다.
-- 체크섬이 바뀌면 mdp.runlog.migrate 가 멈춘다.

-- 작업 한 번 = 한 줄. 무엇을(job · params) 어떤 코드로(code_version) 언제 · 얼마나 돌렸고 어떻게 끝났나.
CREATE TABLE pipeline_runs (
    run_id            uuid PRIMARY KEY,
    job               text        NOT NULL,                 -- 예: bronze.fetch
    status            text        NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz,
    code_version      text        NOT NULL,                 -- git 커밋 (고친 채면 끝에 -dirty)
    params            jsonb       NOT NULL,                 -- 명령줄 인자 그대로
    params_sha256     text        NOT NULL,                 -- params 를 정해진 모양 JSON 으로 만든 해시
    host              text        NOT NULL,
    peak_memory_bytes bigint,                               -- 프로세스 최대 메모리 (모르면 NULL)
    error             text,                                 -- 실패했을 때 오류 한 줄
    CHECK ((status = 'running') = (finished_at IS NULL))    -- 끝난 작업만 끝난 시각이 있다
);
CREATE INDEX pipeline_runs_job_started ON pipeline_runs (job, started_at DESC);

-- 작업이 만든 · 확인한 파일(또는 표) 하나 = 한 줄. 체크섬 · 크기 · 행 수로 '같은 결과인가' 를 본다.
CREATE TABLE run_artifacts (
    run_id        uuid NOT NULL REFERENCES pipeline_runs (run_id) ON DELETE CASCADE,
    artifact_path text NOT NULL,                            -- Bronze 상대 경로 · Delta 표 이름 등
    layer         text NOT NULL CHECK (layer IN ('bronze', 'silver', 'gold')),
    status        text NOT NULL,                            -- 예: downloaded · already_present
    size_bytes    bigint,
    sha256        text,
    row_count     bigint,
    elapsed_s     double precision,
    PRIMARY KEY (run_id, artifact_path)
);
CREATE INDEX run_artifacts_path ON run_artifacts (artifact_path);
