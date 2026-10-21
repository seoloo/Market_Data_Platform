-- Silver · Gold 산출물은 파일이 아니라 Delta 표다. 그 작업이 만든 표 버전을 남겨,
-- '이 실행 기록 = 표의 몇 번째 버전' 을 시간여행(versionAsOf)으로 다시 열 수 있게 한다 (README 4-5).
ALTER TABLE run_artifacts ADD COLUMN table_version bigint;
