"""Delta 표 두 버전 비교 — DuckDB 시간여행으로 행 단위 차이를 센다.

언제 쓰나: 재처리 뒤(:mod:`mdp.reprocess.main`) · 되돌린 뒤 확인할 때. 호스트에서::

    python -m mdp.reprocess.diff --report <재처리 보고 JSON> --against-latest

읽고 쓰는 것: Silver · Gold Delta 표의 두 버전(읽기만) → 화면 · 반환값.

비교 방법 (표마다, 그 종목 · 그 달만):
- 표마다 정한 **키**(예: 체결 번호, 날짜, 분)로 두 버전을 맞대어
  ``추가``(새 버전에만) · ``삭제``(이전에만) · ``변경``(둘 다 있는데 값이 다름)을 센다.
- 변경은 **칸마다** 몇 행이 바뀌었는지 센다 — '의심 표시만 바뀌었고 숫자는 그대로' 같은
  영향 범위가 보인다.
- 실행 번호(``run_id``) · 읽은 Silver 버전(``silver_version``) 같은 계보 칸은 다시 만들면 늘
  바뀌므로 뺀다.

[이유] '다시 만들었다' 만으로는 부족하다 — 무엇이 얼마나 바뀌었고 무엇은 그대로인지 숫자로 남겨야
       재처리를 믿을 수 있다. Delta 는 이전 버전을 지우지 않으므로 바꾸기 전과 후를 나란히 열어
       비교할 수 있다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.query.duck import connect, delta_source
from mdp.serve.bars_1m import latest_delta_version
from mdp.spark.session import table_path

if TYPE_CHECKING:
    import duckdb

__all__ = [
    "TABLES",
    "TableDiff",
    "TableSpec",
    "current_versions",
    "diff_relations",
    "diff_table",
    "main",
]


@dataclass(frozen=True, slots=True)
class TableSpec:
    """[쓰임] 비교할 표 하나 — 계층 · 이름 · 키 칸 · 뺄 계보 칸 · 달을 고르는 칸."""

    layer: str
    name: str
    keys: tuple[str, ...]
    ignore: tuple[str, ...] = ("run_id",)
    month_column: str = "trade_date"
    """``trade_date``(날짜 파티션) 또는 ``period_month``(``YYYY-MM`` 글자)."""

    @property
    def label(self) -> str:
        """[쓰임] ``silver/agg_trades`` 같은 이름."""
        return f"{self.layer}/{self.name}"


TABLES: Final = (
    TableSpec("silver", "agg_trades", ("agg_trade_id",)),
    TableSpec("silver", "agg_trades_audit_daily", ("trade_date",)),
    TableSpec("silver", "agg_trades_audit_file", ("source_path",), month_column="period_month"),
    TableSpec(
        "silver",
        "agg_trades_gaps",
        ("source_path", "kind", "prev_id", "next_id"),
        month_column="period_month",
    ),
    TableSpec(
        "silver",
        "agg_trades_quarantine",
        ("source_path", "reason", "raw_line"),
        month_column="period_month",
    ),
    TableSpec("gold", "bars_1m", ("bar_start_ms",), ("run_id", "silver_version")),
    TableSpec("gold", "bars_1m_kline_check", ("trade_date",)),
)


@dataclass(slots=True)
class TableDiff:
    """[쓰임] 표 하나의 두 버전 차이."""

    table: str
    old_version: int
    new_version: int
    old_rows: int = 0
    new_rows: int = 0
    added: int = 0
    removed: int = 0
    changed: int = 0
    changed_by_column: dict[str, int] = field(default_factory=dict)

    @property
    def identical(self) -> bool:
        """[쓰임] 계보 칸을 빼면 두 버전이 같은가."""
        return self.added == self.removed == self.changed == 0


def current_versions(specs: Sequence[TableSpec] = TABLES) -> dict[str, int]:
    """[쓰임] 표마다 지금(최신) Delta 버전 — 없는 표는 빠진다."""
    versions: dict[str, int] = {}
    for spec in specs:
        path = Path(table_path(spec.layer, spec.name))
        if (path / "_delta_log").is_dir():
            versions[spec.label] = latest_delta_version(path)
    return versions


def _month_where(spec: TableSpec, symbol: str, month: date) -> str:
    """[쓰임] 그 종목 · 그 달 조건."""
    if spec.month_column == "period_month":
        return f"symbol = '{symbol}' AND period_month = '{month:%Y-%m}'"
    next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return (
        f"symbol = '{symbol}' AND trade_date >= DATE '{month:%Y-%m-%d}'"
        f" AND trade_date < DATE '{next_month:%Y-%m-%d}'"
    )


def diff_relations(
    con: duckdb.DuckDBPyConnection,
    old_sql: str,
    new_sql: str,
    keys: Sequence[str],
    label: str = "",
    versions: tuple[int, int] = (0, 0),
) -> TableDiff:
    """[쓰임] 두 SQL 결과(이전 · 새)를 키로 맞대어 추가 · 삭제 · 변경(칸마다)을 센다.
    [이유] Delta 와 상관없는 비교 핵심만 떼어 두면, 작은 표로 시험할 수 있다."""
    con.execute(f"CREATE OR REPLACE TEMP VIEW old_v AS {old_sql}")
    con.execute(f"CREATE OR REPLACE TEMP VIEW new_v AS {new_sql}")
    columns = [row[0] for row in con.execute("DESCRIBE old_v").fetchall()]
    values = [c for c in columns if c not in keys]
    on = " AND ".join(f"o.{k} IS NOT DISTINCT FROM n.{k}" for k in keys)
    per_column = ", ".join(f"count(*) FILTER (WHERE o.{c} IS DISTINCT FROM n.{c})" for c in values)
    any_changed = " OR ".join(f"o.{c} IS DISTINCT FROM n.{c}" for c in values) or "FALSE"
    counts = con.execute(
        f"""SELECT (SELECT count(*) FROM old_v), (SELECT count(*) FROM new_v),
                   (SELECT count(*) FROM new_v n
                    WHERE NOT EXISTS (SELECT 1 FROM old_v o WHERE {on})),
                   (SELECT count(*) FROM old_v o
                    WHERE NOT EXISTS (SELECT 1 FROM new_v n WHERE {on}))"""
    ).fetchone()
    changed = con.execute(
        f"SELECT count(*) FILTER (WHERE {any_changed}){', ' + per_column if per_column else ''}"
        f" FROM old_v o JOIN new_v n ON {on}"
    ).fetchone()
    assert counts is not None and changed is not None
    by_column = {c: int(n) for c, n in zip(values, changed[1:], strict=True) if n}
    return TableDiff(
        label,
        versions[0],
        versions[1],
        int(counts[0]),
        int(counts[1]),
        int(counts[2]),
        int(counts[3]),
        int(changed[0]),
        by_column,
    )


def diff_table(
    con: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    old_version: int,
    new_version: int,
    symbol: str,
    month: date,
) -> TableDiff:
    """[쓰임] 한 Delta 표의 두 버전(그 종목 · 그 달, 계보 칸 뺌)을 :func:`diff_relations` 로
    비교한다."""
    where = _month_where(spec, symbol, month)
    exclude = ", ".join(spec.ignore)

    def version_sql(version: int) -> str:
        source = delta_source(spec.layer, spec.name, version)
        return f"SELECT * EXCLUDE ({exclude}) FROM {source} WHERE {where}"

    return diff_relations(
        con,
        version_sql(old_version),
        version_sql(new_version),
        spec.keys,
        spec.label,
        (old_version, new_version),
    )


def diff_all(
    old_versions: dict[str, int], new_versions: dict[str, int], symbol: str, month: date
) -> list[TableDiff]:
    """[쓰임] 두 버전 묶음에 다 있는 표를 모두 비교한다."""
    con = connect()
    diffs = []
    for spec in TABLES:
        if spec.label in old_versions and spec.label in new_versions:
            diffs.append(
                diff_table(
                    con, spec, old_versions[spec.label], new_versions[spec.label], symbol, month
                )
            )
    con.close()
    return diffs


def log_diffs(diffs: Sequence[TableDiff]) -> None:
    """[쓰임] 표마다 한 줄 JSON 로그."""
    for diff in diffs:
        log_json(event="diff", identical=diff.identical, **asdict(diff))


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 — 재처리 보고의 '이전 버전' 과 지금 최신 버전을 비교한다(되돌린 뒤 확인용).
    [이유] 되돌린 뒤 모든 표가 '이전 버전과 같음' 이면 종료 코드 0, 하나라도 다르면 1."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.reprocess.diff")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--against-latest", action="store_true", required=True)
    args = parser.parse_args(argv)
    report: dict[str, Any] = json.loads(args.report.read_text(encoding="utf-8"))
    year_text, month_text = report["month"].split("-")
    diffs = diff_all(
        report["before"],
        current_versions(),
        report["symbol"],
        date(int(year_text), int(month_text), 1),
    )
    log_diffs(diffs)
    return 0 if all(d.identical for d in diffs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
