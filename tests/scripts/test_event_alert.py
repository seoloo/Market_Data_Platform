"""실적 캘린더는 하루 한 번만 받고, 실패해도 경제 일정 알림은 간다 (가짜 응답)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import event_alert


class _Resp:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"data": {"rows": self._rows}}


def test_calendar_fetched_once_per_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, **_: Any) -> _Resp:
        calls.append(url)
        return _Resp([{"symbol": "QS", "time": "time-after-hours", "marketCap": "$5"}])

    monkeypatch.setattr(event_alert, "EARNINGS", tmp_path / "earnings.json")
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(event_alert, "_held", lambda root: [])
    got = event_alert._earnings(tmp_path, date(2026, 10, 20))
    again = event_alert._earnings(tmp_path, date(2026, 10, 20))
    assert len(calls) == 2  # 오늘 · 내일 한 번씩, 두 번째는 저장본
    assert [(e.day, e.symbol, e.why) for e in got] == [
        (date(2026, 10, 20), "QS", "관심"),
        (date(2026, 10, 21), "QS", "관심"),
    ]
    assert got == again


def test_calendar_failure_gives_no_earnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(url: str, **_: Any) -> _Resp:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(event_alert, "EARNINGS", tmp_path / "earnings.json")
    monkeypatch.setattr(httpx, "get", boom)
    assert event_alert._earnings(tmp_path, date(2026, 10, 20)) == []
