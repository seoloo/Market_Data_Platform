"""되돌리기 — 재처리 보고에 적힌 '이전 버전' 으로 Silver · Gold Delta 표를 RESTORE 한다.

언제 쓰나: 재처리 결과가 마음에 들지 않을 때, Spark 이미지 안에서::

    # <보고> = <lake>/_reports/reprocess/ 아래 재처리 보고 (컨테이너 안 경로 /lake/...)
    docker compose run --rm spark python -m mdp.reprocess.restore --report /lake/<보고>.json

읽고 쓰는 것: 재처리 보고 JSON(읽기) → Delta 표마다 RESTORE(새 버전 하나가 생긴다, 이력은 지우지
              않음).

[이유] Delta 의 RESTORE 는 표를 그 버전의 내용으로 되돌리는 **새 커밋**이다 — 재처리한 버전도 이력에
       남아 '되돌렸다가 다시 앞으로' 도 할 수 있다. 되돌린 뒤에는 :mod:`mdp.reprocess.diff` 로 이전
       버전과 같은지 확인한다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.spark.session import spark_session, table_path

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 보고의 ``before`` 버전마다 ``restoreToVersion`` 하고 새 버전을 로그로 남긴다."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.reprocess.restore")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    from delta.tables import DeltaTable

    report = json.loads(args.report.read_text(encoding="utf-8"))
    spark = spark_session("reprocess.restore")
    spark.sparkContext.setLogLevel("ERROR")
    for label, version in report["before"].items():
        layer, name = label.split("/")
        table = DeltaTable.forPath(spark, table_path(layer, name))
        if int(table.history(1).collect()[0]["version"]) == int(version):
            log_json(event="restore_skip", table=label, version=version, reason="이미 그 버전")
            continue
        table.restoreToVersion(int(version))
        new_version = int(table.history(1).collect()[0]["version"])
        log_json(event="restored", table=label, to_contents_of=version, new_version=new_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
