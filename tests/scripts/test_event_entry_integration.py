"""일정 변경·재검사·보호 매도 연결. 네트워크·실계좌·실제 발송 없음."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tests.ops.test_position_size import _inp
from tests.ops.test_size_order import FakeTrader
from tests.scripts.test_size_guard import Reader, _ms

from auto_trading.ops.entry_events import EntryEvents
from auto_trading.ops.event_calendar import KST
from auto_trading.ops.event_store import EarningsDay
from auto_trading.ops.position_size import recommend
from auto_trading.ops.size_order import Ticket
from scripts import event_alert
from scripts import size_guard as m


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", lambda *a, **k: pytest.fail("외부 네트워크 금지"))


class GuardReader(Reader):
    def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        return dict.fromkeys(symbols, Decimal("96"))

    def usd_krw(self) -> Decimal:
        return Decimal("1400")

    def sellable(self, symbol: str) -> Decimal:
        return Decimal("10")

    def regular_open(self, symbol: str, start_ms: int) -> Decimal:
        return Decimal("100")

    def close(self) -> None:
        pass


class GuardTrader(FakeTrader):
    def close(self) -> None:
        pass


def setup_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tickets: list[Ticket]
) -> GuardTrader:
    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(m, "_reader", GuardReader)
    monkeypatch.setattr(m, "halted_for", lambda *_: None)
    trader = GuardTrader()
    monkeypatch.setattr(m, "_trader", lambda *_: trader)
    m.save_tickets(tickets)
    return trader


def test_new_event_cancels_reservation_without_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = Ticket("r", "NVDA", "NVDA", "USD", 10, 100, 0, session="US:2026-10-15")
    trader = setup_guard(tmp_path, monkeypatch, [ticket])
    monkeypatch.setattr(
        m, "_advise", lambda *a, **k: recommend(_inp(event_risk=EntryEvents(("CPI 보류",))))
    )
    msgs = m.tick(_ms("2026-10-15T22:32:00"), send=False)
    assert not trader.placed and "CPI 보류" in msgs[0]
    assert m.load_tickets()[0].status == "canceled"


def test_stop_runs_before_failing_entry_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = Ticket("h", "HELD", "HELD", "USD", 10, 100, 0, status="holding")
    held.filled, held.avg, held.stop = 10, 100, 97
    new = Ticket("r", "NVDA", "NVDA", "USD", 10, 100, 0, session="US:2026-10-15")
    trader = setup_guard(tmp_path, monkeypatch, [new, held])

    def fail(*args: Any, **kwargs: Any) -> None:
        assert trader.placed and trader.placed[0]["side"] == "SELL"
        raise ValueError("calendar offline")

    monkeypatch.setattr(m, "_advise", fail)
    m.tick(_ms("2026-10-15T22:32:00"), send=False)
    assert [p["side"] for p in trader.placed] == ["SELL"]
    tickets = m.load_tickets()
    assert tickets[0].status == "canceled" and tickets[1].status == "selling"


def test_clean_preflight_buys_without_sending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = Ticket("r", "NVDA", "NVDA", "USD", 10, 100, 0, session="US:2026-10-15")
    trader = setup_guard(tmp_path, monkeypatch, [ticket])
    calls: list[bool] = []

    def fresh(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs["force"])
        return recommend(_inp())

    monkeypatch.setattr(m, "_advise", fresh)
    monkeypatch.setattr(m, "notify", lambda _: pytest.fail("실제 전송 금지"))
    m.tick(_ms("2026-10-15T22:32:00"), send=False)
    assert calls == [True] and trader.placed[0]["side"] == "BUY"
    assert trader.placed[0]["limit"] == 100.3


def test_real_sessions_holiday_early_close_and_next_day() -> None:
    class HolidayReader(GuardReader):
        def checked_regular_session(self, day: str, *, kr: bool) -> tuple[str, str] | None:
            if day == "2026-11-26":
                return None
            if day == "2026-11-27":
                return (f"{day}T23:30:00+09:00", "2026-11-28T03:00:00+09:00")
            return super().checked_regular_session(day, kr=kr)

    reader: Any = HolidayReader()
    days = m._session_days(reader, False, _ms("2026-11-25T20:00:00"))
    assert days == [date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 1)]
    assert not m.session(reader, False, _ms("2026-11-28T03:01:00"))[1]
    assert m._session_days(reader, False, _ms("2026-11-28T03:01:00"))[0] == date(2026, 11, 30)


def test_alert_hides_earnings_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """사용자 2026-10-16 "일정 자동으로 하기 힘들면 그냥 지워줘" — 알림에는 안 넣는다.

    매수 쪽(추천 · 예약)은 여전히 실패를 '발표 모름' 으로 막는다(위 시험들)."""
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


def test_preflight_is_checked_again_before_replacement_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = Ticket("r", "NVDA", "NVDA", "USD", 10, 100, 0, session="US:2026-10-15")
    trader = setup_guard(tmp_path, monkeypatch, [ticket])
    monkeypatch.setattr(m, "_advise", lambda *a, **k: recommend(_inp()))
    monkeypatch.setattr(m, "event_risk", lambda *a, **k: EntryEvents())
    m.tick(_ms("2026-10-15T22:32:00"), send=False)
    assert len(trader.placed) == 1
    trader.fills["o0"] = ("CANCELED", 0, 0)
    monkeypatch.setattr(
        m, "_advise", lambda *a, **k: recommend(_inp(event_risk=EntryEvents(("새 실적 일정",))))
    )
    m.tick(_ms("2026-10-15T22:35:00"), send=False)
    assert len(trader.placed) == 1 and m.load_tickets()[0].status == "canceled"


def test_closed_us_session_uses_next_entry_day_and_shared_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.ops import event_store

    monkeypatch.setattr(m, "REPO", tmp_path)
    config = tmp_path / "config/event_calendar.yaml"
    config.parent.mkdir()
    config.write_text(
        'covered_from: "2026-10-01"\ncovered_until: "2026-12-31"\nverified_on: "2026-10-15"\n'
        'events:\n  - {date: "2026-10-20", time_et: "08:30", key: CPI, name: CPI}\n',
        encoding="utf-8",
    )

    def empty(root: Path, day: date, now_ms: int, **kwargs: Any) -> EarningsDay:
        return event_store.earnings_day(root, day, now_ms, fetch=lambda _: {"data": {"rows": []}})

    monkeypatch.setattr(m, "earnings_day", empty)
    reader: Any = GuardReader()
    result = m.event_risk(reader, "NVDA", _ms("2026-10-17T07:00:00"))
    assert result.ok and result.entry_day == date(2026, 10, 19)
    assert (tmp_path / "data/event_calendar/earnings/2026-10-20.json").exists()
    # 같은 공통 자료를 알림이 재사용한다. 다른 소비자의 캐시로 다시 요청하지 않는다.
    monkeypatch.setattr(event_alert, "_held", lambda _: [])
    now_ms = _ms("2026-10-17T07:00:00")
    monkeypatch.setattr("scripts.event_alert.time.time", lambda: now_ms / 1000)
    assert event_alert._earnings(tmp_path, date(2026, 10, 19)) == []


@pytest.mark.leakage
def test_next_day_advice_does_not_use_unfinished_today_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    class AdviceReader(GuardReader):
        def holdings(self) -> dict[str, Any]:
            return {}

        def buying_power(self, currency: str) -> Decimal:
            return Decimal("100000")

    cutoffs: list[date] = []

    def closes(reader: Any, symbol: str, before_day: date) -> list[float]:
        cutoffs.append(before_day)
        return [100 + i for i in range(220)]

    monkeypatch.setattr(
        m,
        "event_risk",
        lambda *a, **k: EntryEvents(
            entry_day=date(2026, 10, 16), earnings_date="", earnings_in_days=4
        ),
    )
    monkeypatch.setattr(m, "_closes", closes)
    monkeypatch.setattr(m, "_deneb_usd", lambda: 0)
    monkeypatch.setattr(m, "_blocked", dict)
    reader: Any = AdviceReader()
    m._advise(reader, "NVDA", "NVDA", _ms("2026-10-16T04:57:00"))
    assert cutoffs == [date(2026, 10, 15), date(2026, 10, 15)]


def test_calendar_null_and_stale_are_warned_even_on_quiet_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "calendar.yaml"
    path.write_text("null", encoding="utf-8")
    monkeypatch.setattr(event_alert, "CALENDAR", path)
    monkeypatch.setattr(event_alert, "_earnings_status", lambda *a: ([], []))
    text = event_alert._texts(datetime(2026, 10, 20, 17, tzinfo=KST), tmp_path)["premarket"]
    assert text is not None and "확인 불가" in text


def test_each_event_check_keeps_version_and_block_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "REPO", tmp_path)
    result = m._record_event_check("NVDA", 1234, EntryEvents(("CPI 보류",)), {"digest": "fixed"})
    assert not result.ok
    paths = list((tmp_path / "data/event_calendar/checks").glob("*.json"))
    saved = json.loads(paths[0].read_text(encoding="utf-8"))
    assert saved["decision"]["blocked"] == ["CPI 보류"]
    assert saved["rule_version"] == "entry-events-v1" and len(saved["code_digest"]) == 64
