"""배치 병렬 수집 루프 테스트.

2026-09-22 에 이 루프가 없어서 두 번 실패했다 — 전량 제출로 메모리 6.7GB,
단일 거대 트랜잭션으로 다른 프로세스 차단 + 중단 시 전량 손실.
그 둘을 여기서 고정한다.
"""

from __future__ import annotations

import pytest

from auto_trading.marketdata.bulk import run_batched
from auto_trading.storage.db import Database


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "bulk.db") as database:
        database.connection.execute("CREATE TABLE t (n INTEGER PRIMARY KEY)")
        yield database


class TestBatching:
    def test_every_job_runs_once(self, db: Database) -> None:
        seen: list[int] = []
        run_batched(db, list(range(37)), lambda n: n, seen.extend, batch_size=8, workers=4)
        assert sorted(seen) == list(range(37))

    def test_writes_happen_once_per_batch(self, db: Database) -> None:
        batches: list[int] = []
        run_batched(
            db,
            list(range(20)),
            lambda n: n,
            lambda results: batches.append(len(list(results))),
            batch_size=6,
            workers=2,
        )
        assert batches == [6, 6, 6, 2]

    def test_each_batch_is_committed_separately(self, db: Database) -> None:
        """중단돼도 앞 배치는 살아남아야 한다 — 재개 가능성의 전제."""

        def write(results):
            for value in results:
                if value == 7:
                    raise RuntimeError("중단")
                db.connection.execute("INSERT INTO t VALUES (?)", (value,))

        with pytest.raises(RuntimeError, match="중단"):
            run_batched(db, list(range(12)), lambda n: n, write, batch_size=4, workers=2)
        stored = [r[0] for r in db.connection.execute("SELECT n FROM t ORDER BY n")]
        assert stored == [0, 1, 2, 3]  # 첫 배치는 커밋됐고 두 번째는 롤백됐다

    def test_progress_reports_cumulative_counts(self, db: Database) -> None:
        seen: list[tuple[int, int]] = []
        run_batched(
            db,
            list(range(10)),
            lambda n: n,
            lambda _results: None,
            batch_size=4,
            workers=2,
            on_progress=lambda done, total: seen.append((done, total)),
        )
        assert seen == [(4, 10), (8, 10), (10, 10)]

    def test_empty_job_list_does_nothing(self, db: Database) -> None:
        calls: list[object] = []
        run_batched(db, [], lambda n: n, calls.append, batch_size=4)
        assert calls == []

    @pytest.mark.parametrize("size", [0, -3])
    def test_invalid_batch_size_is_rejected(self, db: Database, size: int) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            run_batched(db, [1], lambda n: n, lambda _r: None, batch_size=size)


class TestMemoryDiscipline:
    def test_results_do_not_accumulate_across_batches(self, db: Database) -> None:
        """배치가 끝나면 그 결과를 더 붙들고 있지 않아야 한다.

        전량 제출 방식에서는 완료된 future 가 결과를 붙들어 메모리가 계속 늘었다.
        """
        live: list[int] = []

        class Payload:
            def __init__(self, n: int) -> None:
                self.n = n
                live.append(n)

            def __del__(self) -> None:
                if self.n in live:
                    live.remove(self.n)

        peak = 0

        def write(results) -> None:
            nonlocal peak
            list(results)
            peak = max(peak, len(live))

        run_batched(db, list(range(40)), Payload, write, batch_size=5, workers=2)
        assert peak <= 10, f"배치 크기를 넘어 결과가 쌓였다 (최대 {peak})"
