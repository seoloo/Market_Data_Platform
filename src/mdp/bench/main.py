"""벤치마크 실행 — 엔진 넷을 같은 월 파일로 여러 번 돌려 시간 · 메모리 · 결과 일치를 한 보고로.

언제 쓰나: 호스트에서 한 번 (Spark 는 이 명령이 Docker 로 띄운다)::

    python -m mdp.bench.main --symbol BTCUSDT --month 2024-03 --rounds 2

읽고 쓰는 것:
- 읽기: 체크섬 목록 · Bronze 월 zip (먼저 SHA-256 대조)
- 임시: 압축 푼 CSV ``<lake>/_staging/bench/`` — DuckDB · Spark 가 같은 파일을 읽고, 끝나면 지운다
- 쓰기: 엔진별 원자료 ``<lake>/_reports/bench/raw/*.json`` ·
        보고 ``<lake>/_reports/bench/<종목>-<달>.json`` ·
        실행 기록(작업 ``bench.agg_trades``)

흐름: 1 Bronze 대조 → 2 압축 풀기(한 번, 시간 잼) →
3 판마다 엔진을 차례로(엔진마다 새 프로세스 · 컨테이너)
→ 4 모든 결과를 정답지(``legacy-full``)와 비교 → 5 보고.

[이유] 한 판만 재면 첫 실행의 디스크 읽기(캐시 없음)가 섞인다. 판을 여러 번, 엔진 순서를 섞지 않고
       같은 순서로 돌려 판마다 조건을 같게 하고, 보고에는 판별 값을 모두 남긴다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from mdp.bench.result import compare
from mdp.bench.run import ENGINES
from mdp.bronze.archive import ArchiveFile, Dataset
from mdp.bronze.fetch import (
    DEFAULT_MANIFEST,
    default_bronze_root,
    log_json,
    read_manifest,
    setup_cli_logging,
)
from mdp.runlog.recorder import REPO_ROOT, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.silver.sources import stage_csv, verify_bronze_file
from mdp.spark.session import lake_root

__all__ = ["main"]

REFERENCE = "legacy-full"


def _run_engine(engine: str, csv_path: Path, zip_path: Path, out: Path) -> dict[str, Any]:
    """[쓰임] 엔진 하나를 새 프로세스(Spark 는 새 컨테이너)로 돌리고 결과 JSON 을 읽어 온다.
    [이유] 바깥 시간(``wall_s``)도 잰다 — 파이썬 · 컨테이너를 띄우는 시간까지 든 '명령 한 번' 의
           시간."""
    if engine == "spark":
        lake = lake_root()
        inside_csv = "/lake/" + csv_path.relative_to(lake).as_posix()
        inside_out = "/lake/" + out.relative_to(lake).as_posix()
        command = [
            "docker",
            "compose",
            "run",
            "--rm",
            "spark",
            "python",
            "-m",
            "mdp.bench.run",
            "--engine",
            "spark",
            "--input",
            inside_csv,
            "--out",
            inside_out,
        ]
    else:
        source = zip_path if engine.startswith("legacy") else csv_path
        command = [
            sys.executable,
            "-m",
            "mdp.bench.run",
            "--engine",
            engine,
            "--input",
            str(source),
            "--out",
            str(out),
        ]
    env = {**os.environ, "MSYS_NO_PATHCONV": "1", "PYTHONIOENCODING": "utf-8"}
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    wall = round(time.perf_counter() - started, 2)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{engine} 실패 (코드 {completed.returncode}): {completed.stderr[-1500:]}"
        )
    payload: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    payload["wall_s"] = wall
    return payload


def run_benchmark(
    symbol: str, month: date, rounds: int, engines: Sequence[str], recorder: RunRecorder
) -> dict[str, Any]:
    """[쓰임] 흐름 1 ~ 5 를 돌려 보고 사전을 돌려준다(파일로도 쓴다)."""
    params = {
        "symbol": symbol,
        "month": f"{month:%Y-%m}",
        "rounds": rounds,
        "engines": list(engines),
    }
    reports = lake_root() / "_reports" / "bench"
    staging = lake_root() / "_staging" / "bench"
    with record_run(recorder, "bench.agg_trades", params) as run:
        # 1 단계: Bronze 대조.
        monthly = ArchiveFile(Dataset.AGG_TRADES, symbol, month)
        entry = read_manifest(DEFAULT_MANIFEST).get(monthly.relative_path)
        if entry is None:
            raise FileNotFoundError(f"체크섬 목록에 없다: {monthly.relative_path}")
        zip_path = default_bronze_root().joinpath(*monthly.relative_path.split("/"))
        verify_bronze_file(zip_path, entry[1])

        # 2 단계: 압축 풀기(한 번).
        started = time.perf_counter()
        csv_path = stage_csv(zip_path, staging)
        unzip_s = round(time.perf_counter() - started, 2)
        log_json(
            event="staged", csv=str(csv_path), unzip_s=unzip_s, csv_bytes=csv_path.stat().st_size
        )

        # 3 단계: 판마다 엔진을 차례로.
        runs: list[dict[str, Any]] = []
        try:
            for round_no in range(1, rounds + 1):
                for engine in engines:
                    out = reports / "raw" / f"{symbol}-{month:%Y-%m}-{engine}-r{round_no}.json"
                    payload = _run_engine(engine, csv_path, zip_path, out)
                    payload["round"] = round_no
                    runs.append(payload)
                    log_json(
                        event="bench",
                        engine=engine,
                        round=round_no,
                        elapsed_s=payload["elapsed_s"],
                        wall_s=payload["wall_s"],
                        peak_mib=payload["peak_memory_bytes"] // 2**20,
                        memory_scope=payload["memory_scope"],
                    )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        # 4 단계: 정답지와 비교(판 1 결과끼리).
        first = {r["engine"]: r for r in runs if r["round"] == 1}
        reference = first.get(REFERENCE)
        agreement = {
            engine: (compare(reference["result"], r["result"]) if reference else ["정답지 없음"])
            for engine, r in first.items()
            if engine != REFERENCE
        }
        for engine, differences in agreement.items():
            log_json(
                event="agreement",
                engine=engine,
                reference=REFERENCE,
                same=not differences,
                differences=differences[:5],
            )

        # 5 단계: 보고.
        report = {
            "symbol": symbol,
            "month": f"{month:%Y-%m}",
            "rows": reference["result"]["rows"] if reference else None,
            "zip_bytes": zip_path.stat().st_size,
            "unzip_s": unzip_s,
            "machine": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
            "runs": [{k: v for k, v in r.items() if k != "result"} for r in runs],
            "agreement_vs_reference": agreement,
            "run_id": str(run.run_id),
        }
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / f"{symbol}-{month:%Y-%m}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        log_json(event="report", path=str(path))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 — 엔진 넷(또는 고른 것)을 ``--rounds`` 판 돌린다."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.bench.main")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    year_text, month_text = args.month.split("-")
    month = date(int(year_text), int(month_text), 1)
    engines = [REFERENCE, *[e for e in args.engines if e != REFERENCE]]
    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run_benchmark(args.symbol, month, args.rounds, engines, recorder)
    else:
        run_benchmark(args.symbol, month, args.rounds, engines, NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
