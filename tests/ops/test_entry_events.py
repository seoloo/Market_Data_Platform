from datetime import date, datetime, timedelta

import pytest

from auto_trading.ops.entry_events import company_events, macro_events
from auto_trading.ops.event_calendar import KST
from auto_trading.ops.event_store import EarningsDay


def ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=KST).timestamp() * 1000)


def calendar(key: str = "FOMC", day: str = "2026-10-28") -> dict[str, object]:
    return {
        "covered_from": "2026-10-01",
        "covered_until": "2026-12-31",
        "verified_on": "2026-10-15",
        "events": [{"date": day, "time_et": "14:00", "key": key, "name": key}],
    }


@pytest.mark.parametrize("key", ["CPI", "PCE", "NFP", "FOMC"])
def test_high_events_block_whole_us_session_even_after_release(key: str) -> None:
    risk = macro_events(calendar(key), date(2026, 10, 28), ms("2026-10-29T04:00"))
    assert not risk.ok and "10/29 03:00" in risk.blocked[0]
    assert "다음 정규장" in risk.lines[0]


@pytest.mark.parametrize("key", ["PPI", "GDP"])
def test_caution_keeps_size_unchanged(key: str) -> None:
    risk = macro_events(calendar(key), date(2026, 10, 28), ms("2026-10-28T20:00"))
    assert risk.ok and "비중 자동 변경 없음" in risk.lines[0]


def test_next_session_rechecks_other_event_instead_of_blind_return() -> None:
    raw = calendar()
    assert macro_events(raw, date(2026, 10, 29), ms("2026-10-29T20:00")).ok
    raw["events"] = [
        *raw["events"],  # type: ignore[misc]
        {"date": "2026-10-29", "time_et": "08:30", "key": "PCE", "name": "PCE"},
    ]
    assert not macro_events(raw, date(2026, 10, 29), ms("2026-10-29T20:00")).ok


@pytest.mark.parametrize(
    "change",
    [
        {"covered_from": "2026-11-01"},
        {"covered_until": "2026-10-27"},
        {"verified_on": "2026-01-01"},
        {"verified_on": "2026-11-01"},
        {"events": []},
        {"events": [{"bad": True}]},
        {"covered_from": "bad"},
    ],
)
def test_macro_unknown_is_not_clear(change: dict[str, object]) -> None:
    assert not macro_events({**calendar(), **change}, date(2026, 10, 28), ms("2026-10-28T20:00")).ok


def snapshots(start: date, end: date, event: date | None = None) -> dict[date, EarningsDay]:
    out = {}
    while start <= end:
        rows = ({"symbol": "NVDA", "time": "time-not-supplied"},) if start == event else ()
        out[start] = EarningsDay(start, rows, 1, "Nasdaq", "test")
        start += timedelta(days=1)
    return out


SESSIONS = [date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 1)]


def test_earnings_three_sessions_include_holidays_and_weekend() -> None:
    days = snapshots(SESSIONS[0], SESSIONS[-1], SESSIONS[-1])
    risk = company_events("nvda", SESSIONS, days)
    assert not risk.ok and risk.earnings_in_days == 3
    # Thanksgiving 11/26은 거래일 수에서는 빼되 발표 확인 범위에서는 빼지 않는다.
    holiday = date(2026, 11, 26)
    risk = company_events("NVDA", SESSIONS, snapshots(SESSIONS[0], SESSIONS[-1], holiday))
    assert not risk.ok


def test_normal_empty_differs_from_missing_day() -> None:
    days = snapshots(SESSIONS[0], SESSIONS[-1])
    assert company_events("NVDA", SESSIONS, days).ok
    del days[date(2026, 11, 28)]
    assert not company_events("NVDA", SESSIONS, days).ok


@pytest.mark.parametrize("offset", [0, 1, 2, 3])
def test_today_afterhours_unknown_time_all_block(offset: int) -> None:
    risk = company_events("NVDA", SESSIONS, snapshots(SESSIONS[0], SESSIONS[-1], SESSIONS[offset]))
    assert not risk.ok and risk.earnings_in_days == offset


def test_incomplete_or_unsorted_sessions_are_unknown() -> None:
    days = snapshots(SESSIONS[0], SESSIONS[-1])
    assert not company_events("NVDA", SESSIONS[:3], days).ok
    assert not company_events("NVDA", list(reversed(SESSIONS)), days).ok


@pytest.mark.leakage
def test_later_macro_rows_do_not_change_earlier_session() -> None:
    raw = calendar("CPI", "2026-10-14")
    before = macro_events(raw, date(2026, 10, 15), ms("2026-10-15T20:00"))
    raw["events"] = [
        {"date": "2026-10-14", "time_et": "08:30", "key": "CPI", "name": "CPI"},
        {"date": "2026-10-28", "time_et": "14:00", "key": "FOMC", "name": "FOMC"},
    ]
    assert macro_events(raw, date(2026, 10, 15), ms("2026-10-15T20:00")) == before
