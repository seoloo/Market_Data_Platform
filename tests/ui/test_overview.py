"""운영 홈: 실제 저장 자료, 결측 표시, 예정 시각, 읽기 전용 경계."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_trading.ui.data import load_dashboard
from auto_trading.ui.overview import _upcoming, overview_stocks, overview_summary

_KST = timezone(timedelta(hours=9))
_NOW = int(datetime(2026, 10, 4, 12, tzinfo=_KST).timestamp() * 1000)


def test_missing_reports_are_pending_not_fake_progress(tmp_path: Path) -> None:
    html = overview_stocks(load_dashboard(tmp_path, _NOW))
    assert html.count("첫 실행 대기") == 4
    assert 'role="progressbar"' not in html
    assert "평가 손익은 표시하지 않습니다" in html
    assert not (tmp_path / "data").exists()


def test_saved_progress_without_account_profit_or_writes(tmp_path: Path) -> None:
    folder = tmp_path / "reports" / "toss" / "QNDX"
    folder.mkdir(parents=True)
    path = folder / "2026-10-04.json"
    path.write_text(
        json.dumps(
            {
                "status": "WAIT",
                "advice": {"accrued": 250, "price": 1000},
                "holding_qty": 77,
                "pnl": 987654,
            }
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()
    data = load_dashboard(tmp_path, _NOW)
    html = overview_stocks(data)
    assert 'aria-valuenow="25.0"' in html and "적립 중" in html
    assert "77주" not in html and "987654" not in html
    assert "1/3 보고서" in overview_summary(data)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "accrued,price",
    [
        (None, 1),
        ("bad", 1),
        (-1, 1),
        (1, 0),
        (1, -1),
        (float("nan"), 1),
        (1, float("inf")),
    ],
)
def test_bad_progress_is_not_rendered(tmp_path: Path, accrued: object, price: object) -> None:
    folder = tmp_path / "reports" / "toss" / "QNDX"
    folder.mkdir(parents=True)
    (folder / "2026-10-04.json").write_text(
        json.dumps({"status": "WAIT", "advice": {"accrued": accrued, "price": price}}),
        encoding="utf-8",
    )
    assert 'role="progressbar"' not in overview_stocks(load_dashboard(tmp_path, _NOW))


def test_future_report_excluded_and_corrupt_report_not_pending(tmp_path: Path) -> None:
    folder = tmp_path / "reports" / "toss" / "QNDX"
    folder.mkdir(parents=True)
    (folder / "2026-10-05.json").write_text('{"status":"BUY"}', encoding="utf-8")
    assert "1주 살 차례" not in overview_stocks(load_dashboard(tmp_path, _NOW))
    (folder / "2026-10-04.json").write_text("broken", encoding="utf-8")
    assert "기록 읽기 실패" in overview_stocks(load_dashboard(tmp_path, _NOW))


def test_status_escapes_untrusted_values(tmp_path: Path) -> None:
    folder = tmp_path / "reports" / "toss" / "QNDX"
    folder.mkdir(parents=True)
    (folder / "2026-10-04.json").write_text(
        json.dumps({"status": "<script>alert(1)</script>", "advice": []}),
        encoding="utf-8",
    )
    html = overview_stocks(load_dashboard(tmp_path, _NOW))
    assert "<script" not in html and "상태 미확인" in html


def test_schedule_skips_finished_tracks_and_weekends(tmp_path: Path) -> None:
    rows = _upcoming(load_dashboard(tmp_path, _NOW))
    assert rows[0][0].strftime("%Y-%m-%d %H:%M") == "2026-10-04 21:00"
    assert "선물 실행기 훈련 V4" not in [name for _, name in rows]
    toss = next(at for at, name in rows if "토스" in name)
    assert toss.strftime("%Y-%m-%d %H:%M") == "2026-10-05 21:00"
    late = int(datetime(2026, 10, 4, 23, tzinfo=_KST).timestamp() * 1000)
    assert _upcoming(load_dashboard(tmp_path, late))[0][0].day == 5
