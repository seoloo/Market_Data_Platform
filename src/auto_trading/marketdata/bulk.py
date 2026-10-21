"""대량 병렬 수집 공통 루프 — 작업을 배치로 나눠 병렬로 받고, 배치마다 커밋한다.

언제 쓰나: 여러 종목 · 여러 달을 한꺼번에 받는 수집 스크립트(``scripts/backfill_universe_*.py``).
읽고 쓰는 것: 부르는 쪽이 넘긴 ``work``(받기) · ``write``(저장) 함수가 정한다. 이 루프는 DB 에
              배치마다 커밋하고 WAL 을 잘라 낼 뿐이다.

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
    """[쓰임] ``jobs`` 를 ``batch_size`` 개씩 ``work`` 로 병렬 처리하고, 배치마다 ``write`` → 커밋.
    [이유] ``work`` 는 스레드에서(네트워크), ``write`` 는 메인 스레드에서(SQLite) 돈다 — SQLite
           연결을 스레드끼리 나눠 쓰지 않으려고. 머리말의 두 실패(메모리 · 잠금)를 함께 막는다."""
    if batch_size < 1:
        raise ValueError(f"batch_size 는 1 이상이어야 한다: {batch_size}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch_start in range(0, len(jobs), batch_size):
            # 1 단계: 이번 배치만 병렬로 받는다 (결과가 메모리에 한 배치만 남게)
            batch_jobs = jobs[batch_start : batch_start + batch_size]
            results = list(pool.map(work, batch_jobs))
            # 2 단계: 한 트랜잭션으로 저장 · 커밋, 결과를 비워 메모리를 돌려준다
            with db.transaction():
                write(results)
            results.clear()
            # 3 단계: WAL 을 잘라 파일이 커지지 않게
            db.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if on_progress is not None:
                on_progress(min(batch_start + batch_size, len(jobs)), len(jobs))
