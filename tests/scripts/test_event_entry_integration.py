"""일정 알림(event_alert) — 실적 일정 실패 숨김 · 보유 표 읽기 · 달력 경고.

네트워크 · 실제 발송 없음. 예전에는 실거래 크기 가드(``scripts/size_guard``)와의 연결 시험이 함께
있었다. 실주문 코드를 걷어 낼 때(README 6-3) 그 시험들은 함께 지웠고, 남는 알림 도구 시험만 남겼다.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ops.event_calendar import KST
from auto_trading.ops.event_store import EarningsDay
from scripts import event_alert


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", lambda *a, **k: pytest.fail("외부 네트워크 금지"))


def test_alert_hides_earnings_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """사용자 2026-10-16 "일정 자동으로 하기 힘들면 그냥 지워줘" — 알림에는 안 넣는다."""
    calls: list[date] = []

    def snapshot(root: Path, day: date, now_ms: int, **kwargs: Any) -> EarningsDay:
        calls.append(day)
        return EarningsDay(day, (), now_ms, "test", "test", "실적 확인 실패")

    monkeypatch.setattr(event_alert, "earnings_day", snapshot)
    monkeypatch.setattr(event_alert, "_held", lambda _: [])
    text = event_alert._texts(datetime(2026, 10, 20, 17, tzinfo=KST), tmp_path)["premarket"]
    assert text is None or "실적 확인 실패" not in text
    assert calls == [date(2026, 10, 20), date(2026, 10, 21)]


def test_alert_reads_tickets_only_and_matches_macro_block(tmp_path: Path) -> None:
    path = tmp_path / "data/size_guard/state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "tickets": [
                    {"symbol": "NVDA", "status": "reserved", "session": "US:2026-10-14"},
                    {"symbol": "HELD", "status": "holding"},
                ]
            }
        ),
        encoding="utf-8",
    )
    events, _ = event_alert._load()
    lines = event_alert._ticket_lines(tmp_path, events)
    assert "발표일 신규 매수 보류 대상" in lines[0]
    assert "자동 매도하지" in lines[1] and "새 추천" in lines[-2]


def test_calendar_null_and_stale_are_warned_even_on_quiet_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "calendar.yaml"
    path.write_text("null", encoding="utf-8")
    monkeypatch.setattr(event_alert, "CALENDAR", path)
    monkeypatch.setattr(event_alert, "_earnings_status", lambda *a: ([], []))
    text = event_alert._texts(datetime(2026, 10, 20, 17, tzinfo=KST), tmp_path)["premarket"]
    assert text is not None and "확인 불가" in text
