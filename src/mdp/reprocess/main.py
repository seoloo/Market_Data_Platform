"""재처리 실행 — 규칙 값을 바꿔 Silver → Gold → PostgreSQL 복사를 다시 돌리고 바뀐 것을 보고.

언제 쓰나: 호스트에서 (Spark 단계는 이 명령이 Docker 로 띄운다)::

    python -m mdp.reprocess.main --symbol BTCUSDT --month 2024-03 --volume-tolerance-pct 0.0001

읽고 쓰는 것: Silver · Gold Delta 표(다시 씀, 이전 버전은 이력에 남음) · PostgreSQL ``gold_bars_1m``
              · 보고 ``<lake>/_reports/reprocess/<종목>-<달>-<시각>.json`` · 실행 기록(작업
              ``reprocess``).

흐름:
1. 표마다 지금 버전을 적는다(``before``) — 되돌릴 곳.
2. Silver(바꾼 규칙) → Gold → PostgreSQL 복사를 차례로 다시 돌린다(같은 Bronze, 같은 명령).
3. 표마다 새 버전(``after``)을 적고, ``before`` ↔ ``after`` 를 행 단위로 비교한다
   (:mod:`mdp.reprocess.diff`).
4. 보고를 남긴다 — 되돌리려면 ``python -m mdp.reprocess.restore --report <이 보고>``.

[이유] 단계마다 기존 명령을 그대로 부른다 — 재처리 전용 코드가 따로 있으면 '평소 결과' 와 '재처리
       결과' 가 다른 코드에서 나올 수 있다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.reprocess.diff import current_versions, diff_all, log_diffs
from mdp.runlog.recorder import REPO_ROOT, PostgresRecorder, record_run
from mdp.spark.session import lake_root

__all__ = ["main"]


def _run(command: list[str], label: str) -> None:
    """[쓰임] 단계 명령 하나를 돌리고, 실패하면 마지막 출력과 함께 멈춘다."""
    env = {**os.environ, "MSYS_NO_PATHCONV": "1", "PYTHONIOENCODING": "utf-8"}
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
    tail = [line for line in completed.stdout.splitlines() if line.startswith("{")][-3:]
    log_json(event="step", step=label, returncode=completed.returncode, last_logs=tail)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} 실패: {completed.stderr[-1500:]}")


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 흐름 1 ~ 4 — 바꾼 규칙으로 다시 만들고 표마다 바뀐 것을 보고한다."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.reprocess.main")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument(
        "--volume-tolerance-pct",
        required=True,
        type=float,
        help="Silver 날 대조 의심일 기준(%%) — 바꿔 볼 규칙 값",
    )
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if not args.pg_dsn:
        parser.error("PostgreSQL 주소가 필요하다 (--pg-dsn 또는 MDP_PG_DSN) — 복사 · 실행 기록")
    year_text, month_text = args.month.split("-")
    month = date(int(year_text), int(month_text), 1)
    common = ["--symbol", args.symbol, "--month", args.month]
    rule = {"volume_tolerance_pct": args.volume_tolerance_pct}

    with (
        PostgresRecorder(args.pg_dsn) as recorder,
        record_run(
            recorder, "reprocess", {"symbol": args.symbol, "month": args.month, "rule": rule}
        ) as run,
    ):
        # 1 단계: 되돌릴 곳.
        before = current_versions()
        log_json(event="before", versions=before)
        # 2 단계: 같은 명령으로 다시 만들기.
        spark = ["docker", "compose", "run", "--rm", "spark", "python", "-m"]
        _run(
            [
                *spark,
                "mdp.silver.agg_trades",
                *common,
                "--volume-tolerance-pct",
                str(args.volume_tolerance_pct),
            ],
            "silver",
        )
        _run([*spark, "mdp.gold.bars_1m", *common], "gold")
        _run([sys.executable, "-m", "mdp.serve.bars_1m", *common, "--pg-dsn", args.pg_dsn], "serve")
        # 3 단계: 바뀐 것.
        after = current_versions()
        diffs = diff_all(before, after, args.symbol, month)
        log_diffs(diffs)
        # 4 단계: 보고.
        report: dict[str, Any] = {
            "symbol": args.symbol,
            "month": args.month,
            "rule": rule,
            "before": before,
            "after": after,
            "diffs": [{**asdict(d), "identical": d.identical} for d in diffs],
            "run_id": str(run.run_id),
            "restore": "docker compose run --rm spark python -m mdp.reprocess.restore"
            " --report <이 파일>",
        }
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = lake_root() / "_reports" / "reprocess" / f"{args.symbol}-{args.month}-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        log_json(event="report", path=str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
