from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import pytest
from scripts.audit_fomc_schedule import STATEMENT_DATES
from scripts.run_community_150 import (
    CHICAGO,
    FOMC,
    Bars,
    KrStock,
    _consistent,
    gap_trade,
    random_blocks,
    summary,
    switch_equity,
    tom_days,
    trade_verdict,
)


def test_tom_marks_last_day_and_first_three() -> None:
    dates = ["2024-01-29", "2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02",
             "2024-02-05", "2024-02-06"]  # fmt: skip
    assert tom_days(dates) == [False, False, True, True, True, True, False]


def test_switch_equity_charges_entry_and_exit_once_per_block() -> None:
    eq = switch_equity([0.1, 0.1, 0.0], [0.0, 0.0, 0.01], [True, True, False], 0.01)
    assert eq[-1] == pytest.approx(0.99 * 1.1 * 1.1 * 0.99 * 1.01)


def test_random_blocks_keep_lengths_and_avoid_windows() -> None:
    held = [False] * 10 + [True] * 4 + [False] * 10 + [True] * 4
    for seed in range(20):
        out = random_blocks(held, random.Random(seed))
        assert sum(out) == 8
        assert not any(o and h for o, h in zip(out, held, strict=True))


def test_fomc_list_matches_audited_2021_2022_dates() -> None:
    audited = {d.isoformat() for d in STATEMENT_DATES}
    mine = {d for d in FOMC if d[:4] in ("2021", "2022")}
    assert mine == audited
    assert all(date.fromisoformat(d).weekday() < 5 for d in FOMC)
    assert len(FOMC) == len(set(FOMC)) == 116


def _bars(start: datetime, prices: list[tuple[float, float, float, float]]) -> Bars:
    om = [int((start + timedelta(minutes=15 * i)).timestamp() * 1000) for i in range(len(prices))]
    return Bars(
        om,
        [p[0] for p in prices],
        [p[1] for p in prices],
        [p[2] for p in prices],
        [p[3] for p in prices],
        {m: i for i, m in enumerate(om)},
    )


def test_cme_gap_fades_toward_friday_close_and_fills_at_target() -> None:
    fri = date(2024, 3, 8)  # 다음 일요일(3/10)에 서머타임 시작
    fri_close = datetime(2024, 3, 8, 16, tzinfo=CHICAGO)
    start = (fri_close - timedelta(minutes=15)).astimezone(UTC)
    n = 7 * 24 * 4 + 1
    prices = [(100.0, 100.0, 100.0, 100.0)] * n
    sun = datetime(2024, 3, 8, 17, tzinfo=CHICAGO) + timedelta(days=2)
    i_sun = int((sun.timestamp() - start.timestamp()) // 900)
    for i in range(1, n):
        prices[i] = (102.0, 102.0, 102.0, 102.0)  # 주말 2% 위로
    prices[i_sun + 10] = (102.0, 102.0, 99.5, 101.0)  # 금요일 종가 100 에 닿음
    g = gap_trade(_bars(start, prices), fri)
    assert g is not None
    assert g.side == -1 and g.filled
    assert g.gap == pytest.approx(0.02)
    assert g.ret == pytest.approx(1 - 100 / 102)


def test_cme_gap_small_gap_is_skipped() -> None:
    fri_close = datetime(2024, 3, 8, 16, tzinfo=CHICAGO)
    start = (fri_close - timedelta(minutes=15)).astimezone(UTC)
    prices = [(100.0, 100.2, 100.0, 100.1)] * (7 * 24 * 4 + 1)
    assert gap_trade(_bars(start, prices), date(2024, 3, 8)) is None


def test_corporate_action_check_compares_raw_and_adjusted() -> None:
    s = KrStock(["20240101", "20240102"], [10.0, 10.0], [10.0, 50.0], [1, 1], [1, 1],
                ["00", "00"], {"20240101": 50.0, "20240102": 50.0})  # fmt: skip
    assert not _consistent(s, 1)  # 액면 병합: 원래 가격만 5 배
    s.adj["20240102"] = 250.0
    assert _consistent(s, 1)


def test_trade_verdict_needs_both_halves_and_two_se() -> None:
    nets = [0.01] * 20 + [0.02] * 20
    dates = ["2020"] * 20 + ["2023"] * 20
    assert trade_verdict(nets, dates, "2022", 30)["verdict"] == "PASS"
    assert trade_verdict(nets[:10], dates[:10], "2022", 30)["verdict"] == "DATA_INSUFFICIENT"
    bad = [0.01] * 20 + [-0.001] * 20
    assert trade_verdict(bad, dates, "2022", 30)["verdict"] == "REJECT"


def test_cluster_se_is_wider_when_same_day_events_move_together() -> None:
    xs = [0.05, 0.05, -0.05, -0.05]
    plain = summary(xs)["se"]
    clustered = summary(xs, ["a", "a", "b", "b"])["se"]
    assert clustered > plain
