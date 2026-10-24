"""상태 화면 시험 — 그리기(순수) · Delta 로그 읽기 · 누적 막대 너비 (Spark 없이)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mdp.ui.data import DeltaTable, PageData, Section, collect, delta_tables
from mdp.ui.render import _segment_widths, render

NOW = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)
LAKE_KEYS = ("silver_daily", "quarantine", "gaps", "kline_check", "stream")
PG_KEYS = ("jobs", "runs", "reconcile")


def _page(**sections: Section) -> PageData:
    base = {key: Section(rows=[]) for key in (*LAKE_KEYS, *PG_KEYS)}
    return PageData(NOW, "C:\\repo\\data\\lake", [], base | sections)


def _reconcile_row(symbol: str, matched: int, gap: int) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": date(2024, 1, 1),
        "batch_minutes": matched + gap,
        "matched": matched,
        "repaired": 0,
        "stream_gap": gap,
        "late": 0,
        "duplicate": 0,
        "other": 0,
        "only_stream": 0,
        "match_rate": Decimal(matched) / (matched + gap),
        "trades_not_received": 76,
        "trades_late": 0,
        "stream_source": "replay",
        "reconciled_at": NOW,
    }


def test_render_escapes_values_and_uses_forward_slashes() -> None:
    page = _page(
        quarantine=Section(rows=[{"symbol": "<script>x</script>", "reason": "a&b", "rows": 1}])
    )
    html = render(page)
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html and "a&amp;b" in html
    assert "C:/repo/data/lake" in html  # 역슬래시가 ₩ 로 보이지 않게


def test_render_shows_section_errors_instead_of_failing() -> None:
    page = _page(jobs=Section(error="PostgreSQL 주소가 없다 (MDP_PG_DSN)"))
    html = render(page)
    assert "PostgreSQL 주소가 없다" in html and "작업별 마지막 실행" in html


def test_reconcile_keeps_only_symbols_in_this_lake() -> None:
    daily = [{"symbol": "DEMOUSDT", "trade_date": date(2024, 1, 1), "rows": 1, "suspect": False}]
    page = _page(
        silver_daily=Section(rows=daily),
        reconcile=Section(
            rows=[_reconcile_row("DEMOUSDT", 1434, 6), _reconcile_row("BTCUSDT", 993, 447)]
        ),
    )
    html = render(page)
    assert "DEMOUSDT 2024-01-01" in html and "BTCUSDT" not in html
    assert "99.6%" in html  # 한눈에 — 대사 일치율 (스트림 몫)


def test_status_has_icon_and_word_not_color_only() -> None:
    jobs = [
        {
            "job": "silver.agg_trades",
            "status": "failed",
            "started_at": NOW,
            "seconds": 1.0,
            "runs": 1,
            "code_version": "a" * 40,
        },
    ]
    html = render(_page(jobs=Section(rows=jobs)))
    assert "✕</span>실패" in html
    assert "aaaaaaa<" in html  # 코드 버전은 앞 7 자


def test_tiny_stack_segments_stay_visible_and_widths_add_up() -> None:
    widths = _segment_widths([1434, 0, 6, 0, 0, 0], 562)
    assert widths[2] == pytest.approx(5.0)  # 0.4 % 도 적어도 5px
    assert widths[1] == 0 and sum(widths) == pytest.approx(562)


def test_delta_tables_reads_latest_commit(tmp_path: Path) -> None:
    log = tmp_path / "gold" / "bars_1m" / "_delta_log"
    log.mkdir(parents=True)
    (log / "00000000000000000000.json").write_text(
        json.dumps({"commitInfo": {"timestamp": 1_700_000_000_000, "operation": "WRITE"}}) + "\n",
        encoding="utf-8",
    )
    (log / "00000000000000000001.json").write_text(
        json.dumps({"add": {}})
        + "\n"
        + json.dumps({"commitInfo": {"timestamp": 1_700_000_100_000, "operation": "MERGE"}})
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "gold" / "not_delta").mkdir()
    (table,) = delta_tables(tmp_path)
    assert (table.layer, table.name, table.version, table.operation) == (
        "gold",
        "bars_1m",
        1,
        "MERGE",
    )
    assert table.committed_at == datetime.fromtimestamp(1_700_000_100, UTC)


def test_collect_on_empty_lake_without_postgres(tmp_path: Path) -> None:
    page = collect(None, tmp_path)
    assert page.tables == [] and page.sections["jobs"].error and page.sections["kline_check"].error
    assert "레이크에 Delta 표가 없다" in render(page)


SAMPLE_LAKE = Path(__file__).resolve().parents[2] / "data" / "sample" / "lake"


@pytest.mark.skipif(
    not (SAMPLE_LAKE / "gold" / "bars_1m").exists(), reason="샘플 시연을 돌린 뒤에만"
)
def test_collect_reads_the_sample_lake() -> None:
    page = collect(None, SAMPLE_LAKE)
    names = {f"{t.layer}/{t.name}": t for t in page.tables}
    assert names["silver/agg_trades"].rows == 62_126 and names["gold/bars_1m"].rows == 4_320
    assert [r["reason"] for r in page.sections["quarantine"].rows] == [
        "conflicting_duplicate",
        "outside_period",
        "parse_error",
    ]
    assert isinstance(names["gold/bars_1m"], DeltaTable)
