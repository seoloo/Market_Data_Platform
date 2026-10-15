from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from auto_trading.ops.size_order import Ticket
from scripts import size_guard as m

KST = ZoneInfo("Asia/Seoul")


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=KST).timestamp() * 1000)


class Reader:
    def __init__(self, kr_open: bool = True) -> None:
        self.kr_open = kr_open

    def kr_open_today(self, day: str) -> bool:
        return self.kr_open

    def us_regular_session(self, us_date: str) -> tuple[str, str] | None:
        if us_date == "2026-10-17":  # 토요일
            return None
        return (f"{us_date}T22:30:00+09:00", f"{_next(us_date)}T05:00:00+09:00")


def _next(d: str) -> str:
    return date.fromordinal(date.fromisoformat(d).toordinal() + 1).isoformat()


def test_us_session_window() -> None:
    r = Reader()
    assert m.session(r, False, _ms("2026-10-15T22:30:30")) == ("US:2026-10-15", False, False)
    assert m.session(r, False, _ms("2026-10-15T22:31:00")) == ("US:2026-10-15", True, True)
    assert m.session(r, False, _ms("2026-10-16T04:56:00")) == ("US:2026-10-15", False, True)
    assert m.session(r, False, _ms("2026-10-16T04:59:00")) == ("US:2026-10-15", False, False)


def test_kr_session_window() -> None:
    r = Reader()
    assert m.session(r, True, _ms("2026-10-16T09:00:30"))[1:] == (False, False)
    assert m.session(r, True, _ms("2026-10-16T09:01:00")) == ("KR:2026-10-16", True, True)
    assert m.session(r, True, _ms("2026-10-16T15:16:00"))[1:] == (False, True)
    assert m.session(Reader(kr_open=False), True, _ms("2026-10-16T10:00:00"))[1:] == (
        False,
        False,
    )


def test_trading_days_skip_weekend() -> None:
    fri = date(2026, 10, 16)
    assert m._trading_days(fri, fri) == 0
    assert m._trading_days(fri, date(2026, 10, 19)) == 1  # 월요일


def test_blocked_symbols_from_toss_ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DENEB", "BTQ", "KR_005380", "universe"):
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "TOSS_DATA", tmp_path)
    b = m._blocked()
    assert b["TQQQ"].startswith("Deneb") and b["SCHD"] == "Deneb"
    assert b["BTQ"] == "자동 적립" and b["005380"] == "자동 적립"
    assert "DENEB" not in b and "universe" not in b


def test_tick_without_active_tickets_does_not_call_toss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")

    def boom() -> None:
        raise AssertionError("토스를 부르면 안 됨")

    monkeypatch.setattr(m, "_reader", boom)
    assert m.tick(send=False) == []
    t = Ticket("1", "NVDA", "NVDA", "USD", 1, 1.0, 0, status="closed")
    m.save_tickets([t])
    assert m.tick(send=False) == []
    assert m.load_tickets() == [t]
