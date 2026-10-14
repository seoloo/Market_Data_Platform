"""국장 그림자 Paper 판단일 — 기본 월말, 2 주마다는 15 일 이전 마지막 거래일 + 월말."""

from __future__ import annotations

from scripts.run_lt_kr import biweekly_day, month_end_day

DAYS = [
    "2026-10-13",
    "2026-10-14",
    "2026-10-15",
    "2026-10-16",
    "2026-10-30",
    "2026-11-02",
    "2026-11-13",
    "2026-11-16",
    "2026-11-30",
]


def test_month_end_only_on_last_trading_day() -> None:
    assert [d for i, d in enumerate(DAYS) if month_end_day(DAYS, i)] == ["2026-10-30"]


def test_biweekly_adds_mid_month_day_even_when_15th_is_holiday() -> None:
    got = [d for i, d in enumerate(DAYS) if biweekly_day(DAYS, i)]
    assert got == ["2026-10-15", "2026-10-30", "2026-11-13"]  # 11/15 은 일요일 → 11/13
