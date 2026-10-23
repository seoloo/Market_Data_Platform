"""벤치마크 엔진 하나를 새 프로세스에서 돌리고 시간 · 최대 메모리 · 결과를 JSON 으로 남긴다.

언제 쓰나: :mod:`mdp.bench.main` 이 엔진마다 한 번씩 부른다(직접 부를 수도 있다)::

    python -m mdp.bench.run --engine duckdb --input <csv> --out <json>
    # Spark (컨테이너 안 경로)
    docker compose run --rm spark python -m mdp.bench.run --engine spark --input <csv> --out <json>

읽고 쓰는 것: 입력(zip 또는 CSV) 읽기 → 결과 JSON 쓰기.

메모리 재는 법:
- ``legacy-*`` · ``duckdb``: 이 파이썬 프로세스의 최대 메모리(엔진이 프로세스 안에서 돈다).
- ``spark``: 컨테이너 전체의 최대 메모리(cgroup ``memory.peak``) — Spark 자바 프로세스까지 들어간다.
  다만 컨테이너가 읽은 파일의 페이지 캐시도 들어갈 수 있어, 순수 계산 메모리보다 크게 나올 수 있다.

[이유] 엔진마다 새 프로세스(컨테이너)로 돌려야 최대 메모리가 앞 엔진의 흔적 없이 잰다.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mdp.bench.engines import run_duckdb, run_legacy, run_spark
from mdp.runlog.recorder import REPO_ROOT, peak_memory_bytes

__all__ = ["main"]

ENGINES = ("legacy-fast", "legacy-full", "duckdb", "spark")


def _container_peak_bytes() -> int | None:
    """[쓰임] 컨테이너(cgroup v2)의 최대 메모리. 컨테이너 밖이거나 값이 없으면 None."""
    path = Path("/sys/fs/cgroup/memory.peak")
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 고른 엔진으로 과제를 풀고 ``{엔진, 걸린 시간, 최대 메모리, 결과}`` 를 쓴다."""
    parser = argparse.ArgumentParser(prog="python -m mdp.bench.run")
    parser.add_argument("--engine", required=True, choices=ENGINES)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    result: dict[str, Any]
    if args.engine.startswith("legacy"):
        result = run_legacy(args.input, full=args.engine == "legacy-full", repo_root=REPO_ROOT)
    elif args.engine == "duckdb":
        result = run_duckdb(args.input, threads=args.threads)
    else:
        result = run_spark(str(args.input))
    elapsed = time.perf_counter() - started

    container_peak = _container_peak_bytes() if args.engine == "spark" else None
    payload = {
        "engine": args.engine,
        "elapsed_s": round(elapsed, 2),
        "peak_memory_bytes": container_peak or peak_memory_bytes(),
        "memory_scope": "container" if container_peak else "process",
        "result": result,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
