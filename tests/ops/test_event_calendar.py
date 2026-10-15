"""미국 주요 경제 일정 경고 (가짜 일정 · 시각)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml

from auto_trading.ops.event_calendar import (
    KST,
    due,
    morning_text,
    parse,
    premarket_text,
)

RAW = {
    "covered_until": "2026-12-31",
    "events": [
        {"date": "2026-10-14", "time_et": "08:30", "key": "CPI", "name": "9 월 CPI"},
        {"date": "2026-10-28", "time_et": "14:00", "key": "FOMC", "name": "FOMC"},
        {"date": "2026-11-13", "time_et": "08:30", "key": "PPI", "name": "10 월 PPI"},
        {"date": "2026-11-10", "time_et": "08:30", "key": "CPI", "name": "10 월 CPI"},
    ],
}
EVENTS, UNTIL = parse(RAW)


def _kst(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_parse_sorts_and_converts_to_kst_with_dst() -> None:
    assert [e.day for e in EVENTS] == [
        date(2026, 10, 14),
        date(2026, 10, 28),
        date(2026, 11, 10),
        date(2026, 11, 13),
    ]
    assert EVENTS[0].at_kst.strftime("%m-%d %H:%M") == "10-14 21:30"  # 서머타임(EDT)
    assert EVENTS[2].at_kst.strftime("%m-%d %H:%M") == "11-10 22:30"  # 11/1 뒤 EST
    assert EVENTS[1].at_kst.strftime("%m-%d %H:%M") == "10-29 03:00"  # FOMC 는 다음 날 새벽


def test_cpi_day_premarket_warns_to_hold_new_buys() -> None:
    """10/14 프리마켓에 샀다가 −8% — 이 알림이 16:30 에 갔어야 했다."""
    text = premarket_text(_kst(2026, 10, 14, 16, 30), EVENTS, UNTIL)
    assert text is not None and "새 매수 보류" in text and "21:30" in text and "9 월 CPI" in text


def test_morning_mentions_tonight_and_tomorrow_big_events() -> None:
    today = morning_text(_kst(2026, 10, 14, 7, 35), EVENTS, UNTIL)
    assert today is not None and "오늘 밤" in today and "보류" in today
    eve = morning_text(_kst(2026, 10, 27, 7, 35), EVENTS, UNTIL)
    assert eve is not None and "내일 밤" in eve and "10/29 03:00" in eve and "오늘 밤" not in eve
    assert morning_text(_kst(2026, 10, 20, 7, 35), EVENTS, UNTIL) is None


def test_ppi_is_size_caution_not_hold() -> None:
    text = premarket_text(_kst(2026, 11, 13, 17, 0), EVENTS, UNTIL)
    assert text is not None and "크기 주의" in text and "보류" not in text


def test_quiet_day_sends_nothing_and_stale_calendar_warns() -> None:
    assert premarket_text(_kst(2026, 10, 15, 16, 30), EVENTS, UNTIL) is None
    late = parse({**RAW, "covered_until": "2026-10-01"})
    text = premarket_text(_kst(2026, 10, 14, 16, 30), *late)
    assert text is not None and "갱신이 필요" in text


def test_due_once_per_slot_per_day() -> None:
    now = _kst(2026, 10, 14, 7, 40)
    assert due(now, []) == ["2026-10-14:morning"]
    assert due(now, ["2026-10-14:morning"]) == []
    assert due(_kst(2026, 10, 14, 16, 31), ["2026-10-14:morning"]) == ["2026-10-14:premarket"]
    assert due(_kst(2026, 10, 14, 6, 0), []) == []
    assert due(_kst(2026, 10, 14, 23, 45), []) == []


def test_real_calendar_file_parses_and_is_ordered() -> None:
    path = Path(__file__).resolve().parents[2] / "config" / "event_calendar.yaml"
    events, until = parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert until is not None and len(events) >= 20
    assert all(e.time_et.minute in (0, 30) for e in events)
    assert all(e.day.weekday() < 5 for e in events)  # 미국 평일


def test_real_calendar_includes_october_jobs_cpi_and_ppi() -> None:
    """공식 10월 일정이 있어도 로컬 표에서 빠져 조용히 누락되는 것을 막는다."""
    path = Path(__file__).resolve().parents[2] / "config" / "event_calendar.yaml"
    events, _ = parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    actual = {(e.day, e.key, e.at_kst.strftime("%H:%M")) for e in events}
    assert {
        (date(2026, 10, 2), "NFP", "21:30"),
        (date(2026, 10, 14), "CPI", "21:30"),
        (date(2026, 10, 15), "PPI", "21:30"),
    } <= actual


def test_real_calendar_ppi_and_pce_appear_in_notice_without_sending() -> None:
    path = Path(__file__).resolve().parents[2] / "config" / "event_calendar.yaml"
    events, until = parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    ppi = premarket_text(_kst(2026, 10, 15, 16, 30), events, until)
    pce = morning_text(_kst(2026, 10, 29, 7, 35), events, until)
    assert ppi is not None and "생산자물가(PPI)" in ppi and "21:30 KST" in ppi
    assert pce is not None and "PCE" in pce and "21:30 KST" in pce


def test_real_calendar_november_ppi_notice_uses_standard_time() -> None:
    path = Path(__file__).resolve().parents[2] / "config" / "event_calendar.yaml"
    events, until = parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    text = premarket_text(_kst(2026, 11, 13, 16, 30), events, until)
    assert text is not None and "생산자물가(PPI)" in text and "22:30 KST" in text


ROWS = [
    {"symbol": "TSM", "name": "TSMC", "time": "time-pre-market", "marketCap": "$2,308,617,010,000"},
    {"symbol": "CIEN", "name": "Ciena", "time": "time-after-hours", "marketCap": "$20,000,000,000"},
    {"symbol": "QS", "name": "QuantumScape", "time": "time-not-supplied", "marketCap": "$5,000"},
    {"symbol": "XYZ", "name": "Small", "time": "time-pre-market", "marketCap": "$9,000,000,000"},
    {"symbol": "BAD", "name": "No cap", "time": "", "marketCap": "N/A"},
]


def test_earnings_pick_held_watch_and_mega_caps_only() -> None:
    from auto_trading.ops.event_calendar import parse_earnings

    got = parse_earnings(ROWS, date(2026, 10, 20), held=["cien"], watch=["QS"])
    assert [(e.symbol, e.why, e.timing) for e in got] == [
        ("CIEN", "보유", "장 마감 뒤"),
        ("QS", "관심", "시각 미정"),
        ("TSM", "대형주", "장 전"),
    ]
    assert [e.high for e in got] == [True, True, False]


def test_held_earnings_tonight_holds_new_buys_and_mega_cap_is_caution() -> None:
    from auto_trading.ops.event_calendar import parse_earnings

    day = date(2026, 10, 20)
    held = parse_earnings(ROWS, day, held=["CIEN"], watch=[])
    pre = premarket_text(_kst(2026, 10, 20, 16, 30), EVENTS, UNTIL, held)
    assert pre is not None and "새 매수 보류" in pre and "CIEN 실적 (장 마감 뒤) · 보유" in pre
    assert "오늘 CIEN 를 사면" in pre
    eve = morning_text(_kst(2026, 10, 19, 7, 35), EVENTS, UNTIL, held)
    assert eve is not None and "내일 밤" in eve and "CIEN" in eve
    assert "TSM" not in eve  # 내일 밤은 보유 · 관심 실적만
    assert "보유 종목 실적" in eve
    mega = parse_earnings(ROWS, day, held=[], watch=[])
    only = premarket_text(_kst(2026, 10, 20, 16, 30), EVENTS, UNTIL, mega)
    assert only is not None and "크기 주의" in only and "나스닥 전체" in only
