"""대량 병렬 수집 공통 루프.

2026-09-22 에 두 가지로 실패했고, 그 둘을 한 곳에서 막기 위해 만들었다.

1. **모든 작업을 한 번에 제출하면 메모리가 터진다.** ``ThreadPoolExecutor`` 의
   future 는 결과를 붙들고 있어서, 1,776개를 한꺼번에 제출하니 완료된 결과가
   전부 살아남아 6.7GB 까지 올라갔다. 그래서 **배치 단위로 제출**한다.
2. **하나의 거대한 트랜잭션은 다른 프로세스를 막고, 죽으면 전부 잃는다.**
   같은 DB 에 두 수집기를 동시에 돌렸더니 뒤에 것이 쓰기 잠금을 못 얻어 한 건도
   진행하지 못했다. 그래서 **배치마다 커밋**한다 — 재개도 가능해진다.

WAL 은 배치마다 잘라낸다. 자르지 않으면 장시간 수집에서 1GB 를 넘긴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from auto_trading.storage.db import Database

__all__ = ["DEFAULT_BATCH", "run_batched"]

Job = TypeVar("Job")
Result = TypeVar("Result")

DEFAULT_BATCH = 24
"""한 배치의 작업 수.

작업자 수보다 넉넉히 크되, 배치 하나의 결과가 메모리에 다 올라와도 괜찮을 만큼
작아야 한다. 24 × 한 달치 지표(약 8,900건) ≈ 21만 건 수준이다.
"""


def run_batched(
    db: Database,
    jobs: Sequence[Job],
    work: Callable[[Job], Result],
    write: Callable[[Iterable[Result]], None],
    *,
    workers: int = 16,
    batch_size: int = DEFAULT_BATCH,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    """``jobs`` 를 배치로 나눠 병렬 수행하고 배치마다 커밋한다.

    ``work`` 는 스레드에서 돌고 (네트워크), ``write`` 는 메인 스레드에서 돈다 (SQLite).
    SQLite 연결을 스레드 간에 공유하지 않기 위한 분리다.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size 는 1 이상이어야 한다: {batch_size}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(jobs), batch_size):
            chunk = jobs[start : start + batch_size]
            results = list(pool.map(work, chunk))
            with db.transaction():
                write(results)
            results.clear()
            db.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if on_progress is not None:
                on_progress(min(start + batch_size, len(jobs)), len(jobs))
