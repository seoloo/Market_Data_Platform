"""일일 점검의 토스 실거래 + 주식 Paper 부분 (ops/stock_watch.py)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from auto_trading.ops.stock_watch import check_stock_papers, check_toss, expected_toss_day

KST = timezone(timedelta(hours=9))


def _w(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _start(root: Path, day: str = "2026-10-04") -> None:
    for sym in ("QNDX", "BTQ", "WQTM"):
        _w(root / "data" / "toss" / f"{sym}.json", {"start_date": day})


def test_expected_day_skips_weekend_and_waits_for_run() -> None:
    assert expected_toss_day(datetime(2026, 10, 5, 21, 20, tzinfo=KST)) == date(
        2026, 10, 5
    )  # 월 밤
    assert expected_toss_day(datetime(2026, 10, 5, 20, 0, tzinfo=KST)) == date(
        2026, 10, 2
    )  # 월 저녁 → 금
    assert expected_toss_day(datetime(2026, 10, 4, 21, 20, tzinfo=KST)) == date(
        2026, 10, 2
    )  # 일 → 금
    assert expected_toss_day(datetime(2026, 10, 10, 9, 0, tzinfo=KST)) == date(
        2026, 10, 9
    )  # 토 → 금


def test_before_start_and_first_day(tmp_path: Path) -> None:
    lines, found = check_toss(tmp_path, datetime(2026, 10, 5, 21, 20, tzinfo=KST))
    assert lines == [] and found[0].level == "INFO"
    _start(tmp_path)
    _, found = check_toss(
        tmp_path, datetime(2026, 10, 4, 21, 20, tzinfo=KST)
    )  # 일요일 — 아직 예정 없음
    assert found == []
    _, found = check_toss(tmp_path, datetime(2026, 10, 5, 21, 20, tzinfo=KST))  # 월 밤 실행이 없다
    assert [f.level for f in found] == ["WARN"] and "밤 실행 기록 없음" in found[0].message


def test_rejected_orders_and_stuck_qs(tmp_path: Path) -> None:
    _start(tmp_path)
    base = tmp_path / "reports" / "toss"
    for d in ("2026-10-05", "2026-10-06"):
        _w(base / f"{d}.json", {"message": "x"})
        _w(
            base / "QS_SWITCH" / f"{d}.json",
            {"trend_on": True, "want": "BUY", "strategy_tqqq_qty": 0},
        )
    _w(
        base / "QNDX" / "2026-10-06.json",
        {"order": {"status": "REJECTED", "code": "insufficient-buying-power"}},
    )
    _w(base / "BTQ" / "2026-10-06.json", {"order": {"status": "SENT"}})
    lines, found = check_toss(tmp_path, datetime(2026, 10, 6, 21, 20, tzinfo=KST))
    msgs = [f.message for f in found]
    assert any("QNDX 주문 거절 insufficient-buying-power" in m for m in msgs)
    assert any("이틀째" in m for m in msgs)
    assert (
        "마지막 실행 2026-10-06" in lines[0] and "BTQ SENT" in lines[0] and "할 일 BUY" in lines[0]
    )


def test_healthy_run_has_no_findings(tmp_path: Path) -> None:
    _start(tmp_path)
    base = tmp_path / "reports" / "toss"
    _w(base / "2026-10-05.json", {"message": "x"})
    _w(base / "QS_SWITCH" / "2026-10-05.json", {"trend_on": True, "want": "NONE"})
    _, found = check_toss(tmp_path, datetime(2026, 10, 5, 21, 20, tzinfo=KST))
    assert found == []


def test_stock_papers_lag(tmp_path: Path) -> None:
    _w(tmp_path / "reports" / "lt_paper" / "latest.json", {"date": "2026-10-02"})
    _w(tmp_path / "reports" / "qs_paper" / "latest.json", {"date": "2026-09-28"})
    _w(tmp_path / "reports" / "ag_paper" / "latest.json", {"date": "2026-10-03"})
    lines, found = check_stock_papers(tmp_path, datetime(2026, 10, 5, 21, 20, tzinfo=KST))
    assert lines == ["주식 Paper 마지막 기록: LT 10-02 · QS 09-28 · AG 10-03"]
    assert [f.ledger for f in found] == ["QS Paper"]  # LT 3 일(주말) 은 정상, QS 7 일 멈춤
