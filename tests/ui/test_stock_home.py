"""주식 홈의 실제 기록·결측·통화·읽기 전용 성과 경계를 검증한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto_trading.ui.charts import Series
from auto_trading.ui.data import load_dashboard
from auto_trading.ui.render import render_page
from auto_trading.ui.stock_home import _history, _transformed, glide_card, stock_performance

NOW = int(datetime(2026, 10, 9, 12, tzinfo=UTC).timestamp() * 1000)


def _save(root: Path, relative: str, value: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_stock_first_home_and_no_fake_live_curve(tmp_path: Path) -> None:
    html = render_page(load_dashboard(tmp_path, NOW), "overview")
    assert html.index("Glide</h2>") < html.index("수익률 추이") < html.index("토스 적립 현황")
    tabs = html.split('aria-label="주 메뉴">')[1].split("</div>")[0]
    assert tabs.index('href="/toss"') < tabs.index('href="/compare"')
    assert "실거래 평가 기록 대기" in html
    assert "시안용" not in html and "QQQ 비교 기준" not in html
    assert "<script" not in html
    assert not (tmp_path / "data").exists()


def test_glide_uses_fill_evidence_not_deposit(tmp_path: Path) -> None:
    path = _save(
        tmp_path,
        "data/toss/M3.json",
        {
            "amounts": {"2026-10": 450000},
            "deposits": [["2026-10-09", 450000]],
            "orders": [{"filled_qty": 0, "status": "SENT"}],
            "pnl": 778899,
        },
    )
    before = path.read_bytes()
    html = glide_card(load_dashboard(tmp_path, NOW))
    assert "450,000" in html and "첫 체결 확인 대기" in html
    assert "778899" not in html and path.read_bytes() == before
    _save(tmp_path, "data/toss/M3.json", {"orders": [{"filled_qty": 1}]})
    assert "체결 기록 있음" in glide_card(load_dashboard(tmp_path, NOW))


def test_corrupt_glide_not_presented_as_pending(tmp_path: Path) -> None:
    path = _save(tmp_path, "data/toss/M3.json", {})
    path.write_text("broken", encoding="utf-8")
    assert "장부 읽기 실패" in glide_card(load_dashboard(tmp_path, NOW))


@pytest.mark.parametrize(
    "history",
    [
        None,
        [["bad", 1000]],
        [["2026-10-01", -1]],
        [["2026-10-01", float("nan")]],
        [["2026-10-01", 1000], ["2026-10-01", 1001]],
        [["2026-10-02", 1000], ["2026-10-01", 1001]],
        [["2026-10-01"]],
    ],
)
def test_bad_history_is_not_joined_into_a_curve(history: object) -> None:
    assert _history(history, NOW) is None


def test_future_history_is_excluded() -> None:
    history = [["2026-10-08", 1000], ["2026-10-10", 999999]]
    assert _history(history, NOW) == ((NOW - 129600000, 1000),)


def test_roi_period_crop_does_not_reset_base_or_drawdown() -> None:
    series = Series("paper", "blue", ((1, 1000), (2, 1200), (3, 900)))
    assert _transformed(series, "return", 3).points[0][1] == pytest.approx(-10)
    assert _transformed(series, "dd", 3).points == ((3, -25),)
    assert _transformed(series, "equity", 3).points == ((3, 900),)


def test_real_saved_paper_data_only_separate_ccy_and_no_writes(tmp_path: Path) -> None:
    path = _save(
        tmp_path,
        "data/mf_paper/state.json",
        {
            "books": {
                "LD_M3": {"ccy": "USD", "history": [["2026-10-07", 1000], ["2026-10-08", 1020]]},
                "NBG_ISA": {"ccy": "KRW", "history": [["2026-10-07", 87654321]]},
                "BASE": {"ccy": "USD", "history": [["2026-10-10", 7654321]]},
            }
        },
    )
    before = path.read_bytes()
    html = stock_performance(load_dashboard(tmp_path, NOW))
    assert "Glide 비교 · 변동성" in html and "<polyline" in html
    assert "87654321" not in html and "7654321" not in html
    assert "perf-live-content" in html and "실거래 평가 기록 대기" in html
    assert 'id="perf-live" type="radio" name="perf-source" checked' in html
    assert "첫 저장 평가액 기준" in html and "첫 평가 이전 비용 제외" in html
    assert path.read_bytes() == before


def test_corrupt_paper_schema_reports_problem(tmp_path: Path) -> None:
    _save(
        tmp_path,
        "data/mf_paper/state.json",
        {"books": {"LD_M3": {"ccy": "USD", "history": [["bad", 1]]}}},
    )
    html = stock_performance(load_dashboard(tmp_path, NOW))
    assert "평가 이력 오류" in html and "<polyline" not in html
