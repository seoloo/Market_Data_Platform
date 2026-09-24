"""유휴 현금 이자 (Paper 보고용 가정, docs/research/29 C1)."""

from __future__ import annotations

import pytest

from auto_trading.core.cash_yield import RateSchedule, Tier, daily_interest, with_cash_yield

D = 86_400_000
TIERS = (Tier(1000.0, 0.0708), Tier(None, 0.0308))


def test_tiers_split_the_balance() -> None:
    assert daily_interest(500, TIERS) == pytest.approx(500 * 0.0708 / 365)
    assert daily_interest(10_000, TIERS) == pytest.approx((1000 * 0.0708 + 9000 * 0.0308) / 365)
    assert daily_interest(0, TIERS) == 0.0
    assert daily_interest(-5, TIERS) == 0.0


def test_no_idle_cash_leaves_the_ledger_unchanged() -> None:
    schedule = RateSchedule(((0, TIERS),))
    equity = [10_000.0, 10_100.0, 9_900.0]
    assert with_cash_yield([0, D, 2 * D], equity, [0.0, 0.0, 0.0], schedule) == equity


def test_all_cash_compounds_daily() -> None:
    schedule = RateSchedule(((0, (Tier(None, 0.0365),)),))
    days = [k * D for k in range(366)]
    path = with_cash_yield(days, [10_000.0] * 366, [1.0] * 366, schedule)
    assert path[1] == pytest.approx(10_001.0)
    assert path[-1] == pytest.approx(10_000 * 1.0001**365)


def test_returns_follow_the_ledger_and_interest_uses_yesterdays_idle() -> None:
    schedule = RateSchedule(((0, (Tier(None, 0.365),)),))  # 하루 0.1%
    path = with_cash_yield([0, D], [10_000.0, 11_000.0], [0.5, 0.0], schedule)
    # 원장 +10% · 전날 유휴 5,000 × 0.1% = 5
    assert path[1] == pytest.approx(11_000 + 5)


def test_rate_changes_apply_from_their_date() -> None:
    schedule = RateSchedule(((0, (Tier(None, 0.365),)), (2 * D, (Tier(None, 0.0),))))
    path = with_cash_yield([0, D, 2 * D, 3 * D], [100.0] * 4, [1.0] * 4, schedule)
    assert path[1] == pytest.approx(100.1)
    assert path[2] == path[3]  # 2일째부터 0%
    assert schedule.tiers_on(-D) == schedule.tiers_on(0)  # 첫 항목 이전은 첫 항목


def test_schedule_must_be_sorted_and_non_empty() -> None:
    with pytest.raises(ValueError):
        RateSchedule(())
    with pytest.raises(ValueError):
        RateSchedule(((D, TIERS), (0, TIERS)))
