"""스트림 봉 = 배치 봉? 빠르게 맞대 보기 — DuckDB 로 ``gold/stream_bars_1m`` 과 ``gold/bars_1m``.

언제 쓰나: 스트림 처리 뒤, 호스트 · Airflow 컨테이너에서 (Spark 없이)::

    python -m mdp.stream.check --symbol BTCUSDT --start 2024-03-01T00:00 --end 2024-03-02T00:00

읽고 쓰는 것: Delta 두 표(읽기만) · 표준 출력 JSON 한 줄. 같으면 종료 코드 0, 하나라도 다르면 1.

비교: [start, end) 의 분마다 두 봉을 (종목, 분)으로 맞대어
- 둘 다 있고 :data:`~mdp.gold.bars_1m.BAR_COLUMNS` 가 모두 같음 / 하나라도 다름(칸마다 셈)
- 배치에만 있음(스트림이 놓침 · 아직 확정 안 됨) / 스트림에만 있음

[이유] 날마다 원인을 나눠 PostgreSQL 에 남기는 정식 대사는 5-2 에서 한다. 여기서는 '같은 원문 ·
       같은 집계식이면 같은 봉' 을 숫자로 확인한다.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.gold.bars_1m import BAR_COLUMNS
from mdp.query.duck import connect, delta_source
from mdp.stream.bars_1m import STREAM_TABLE

__all__ = ["compare_bars", "main"]


def compare_bars(con: Any, symbol: str, start: datetime, end: datetime) -> dict[str, Any]:
    """[쓰임] 위 비교 결과 (분 수 · 칸마다 다른 분 수)."""
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    where = f"symbol = $symbol AND bar_start_ms >= {start_ms} AND bar_start_ms < {end_ms}"
    differs = {c: f"b.{c} IS DISTINCT FROM s.{c}" for c in BAR_COLUMNS}
    any_differs = " OR ".join(f"d_{c}" for c in BAR_COLUMNS)
    per_column = ", ".join(f"count(*) FILTER (WHERE in_both AND d_{c}) AS {c}" for c in BAR_COLUMNS)
    sql = f"""
        WITH b AS (SELECT * FROM {delta_source("gold", "bars_1m")} WHERE {where}),
             s AS (SELECT * FROM {delta_source("gold", STREAM_TABLE)} WHERE {where}),
             j AS (SELECT b.bar_start_ms IS NOT NULL AND s.bar_start_ms IS NOT NULL AS in_both,
                          b.bar_start_ms IS NULL AS only_stream,
                          s.bar_start_ms IS NULL AS only_batch,
                          {", ".join(f"({cond}) AS d_{c}" for c, cond in differs.items())}
                   FROM b FULL OUTER JOIN s
                     ON b.symbol = s.symbol AND b.bar_start_ms = s.bar_start_ms)
        SELECT count(*) FILTER (WHERE in_both) AS both_minutes,
               count(*) FILTER (WHERE in_both AND NOT ({any_differs})) AS equal_minutes,
               count(*) FILTER (WHERE only_batch) AS only_batch,
               count(*) FILTER (WHERE only_stream) AS only_stream,
               {per_column}
        FROM j
    """
    row = con.execute(sql, {"symbol": symbol}).fetchone()
    names = ["both_minutes", "equal_minutes", "only_batch", "only_stream", *BAR_COLUMNS]
    values = dict(zip(names, row, strict=True))
    return {
        "both_minutes": values["both_minutes"],
        "equal_minutes": values["equal_minutes"],
        "only_batch": values["only_batch"],
        "only_stream": values["only_stream"],
        "differs_by_column": {c: values[c] for c in BAR_COLUMNS if values[c]},
    }


def _parse_time(text: str) -> datetime:
    """[쓰임] ``2024-03-01T00:00`` (UTC) → 시각."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 — 결과 JSON 한 줄, 모두 같으면 0."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.stream.check")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, type=_parse_time)
    parser.add_argument("--end", required=True, type=_parse_time)
    args = parser.parse_args(argv)
    con = connect()
    result = compare_bars(con, args.symbol, args.start, args.end)
    con.close()
    log_json(event="stream_check", symbol=args.symbol, **result)
    same = result["equal_minutes"] == result["both_minutes"]
    return 0 if same and not result["only_batch"] and not result["only_stream"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
