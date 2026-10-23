"""재처리 비교 시험 — 추가 · 삭제 · 칸별 변경을 세는지, 같은 내용의 두 실제 버전이 '같음' 인지."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from mdp.reprocess.diff import TABLES, diff_relations, diff_table  # noqa: E402

OLD = "SELECT * FROM (VALUES (1, 10.0, FALSE), (2, 20.0, FALSE), (3, 30.0, FALSE)) t(k, v, flag)"
NEW = "SELECT * FROM (VALUES (1, 10.0, FALSE), (2, 20.0, TRUE), (4, 40.0, FALSE)) t(k, v, flag)"


def test_counts_added_removed_and_changed_columns() -> None:
    con: Any = duckdb.connect()
    diff = diff_relations(con, OLD, NEW, ("k",), "t", (0, 1))
    assert (diff.old_rows, diff.new_rows, diff.added, diff.removed, diff.changed) == (3, 3, 1, 1, 1)
    assert diff.changed_by_column == {"flag": 1}  # 값(v)은 그대로, 표시만 바뀜
    assert not diff.identical


def test_same_contents_are_identical() -> None:
    con: Any = duckdb.connect()
    assert diff_relations(con, OLD, OLD, ("k",)).identical


def test_every_table_spec_has_keys_and_lineage_columns_ignored() -> None:
    labels = {spec.label for spec in TABLES}
    assert {"silver/agg_trades", "gold/bars_1m"} <= labels
    assert all(spec.keys and "run_id" in spec.ignore for spec in TABLES)


REAL = Path(__file__).resolve().parents[2] / "data" / "lake" / "silver" / "agg_trades_audit_daily"


@pytest.mark.skipif(
    not (REAL / "_delta_log" / "00000000000000000001.json").exists(),
    reason="실제 Silver 표(두 버전) 없음",
)
def test_real_rebuilt_audit_versions_are_identical() -> None:
    from mdp.query.duck import connect

    spec = next(s for s in TABLES if s.name == "agg_trades_audit_daily")
    # 4-1 의 첫 판(v0)과 최적화 판(v1) — 같은 규칙으로 다시 만들었으므로 같아야 한다
    assert diff_table(connect(), spec, 0, 1, "BTCUSDT", date(2024, 3, 1)).identical
