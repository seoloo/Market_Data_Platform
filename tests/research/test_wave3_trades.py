"""문서 95 거래 재현 — 체결·손절·목표·시간 청산·연장 취소·교체·불리한 순서 가정."""

from __future__ import annotations

import pytest

from auto_trading.research.elliott.wave3_trades import Bar, Rules, Setup, simulate

M = 60_000
# 롱 셋업: A 100 → B 120, 진입 110, 손절 100, 목표 110 + 1.618 × 20 = 142.36
LONG = Setup(as_of_ms=0, direction=1, entry=110.0, stop=100.0, target=142.36, b=120.0)
SHORT = Setup(as_of_ms=0, direction=-1, entry=110.0, stop=120.0, target=77.64, b=100.0)
NO_COST = Rules(valid_minutes=10, hold_minutes=20, cost_side=0.0)


def bars(*rows: tuple[float, float, float, float]) -> list[Bar]:
    return [Bar((i + 1) * M, o, h, low, c) for i, (o, h, low, c) in enumerate(rows)]


def test_fill_then_target_is_plus_3_236_r() -> None:
    trades, counts = simulate([LONG], bars((115, 116, 109, 112), (112, 150, 111, 145)), NO_COST)
    (t,) = trades
    assert counts.filled == 1 and t.reason == "TARGET"
    assert t.r_gross == pytest.approx(3.236)


def test_touch_is_not_a_fill_and_extension_cancels() -> None:
    trades, counts = simulate([LONG], bars((115, 116, 110, 112), (112, 121, 111, 120)), NO_COST)
    assert trades == [] and counts.cancelled_extension == 1 and counts.filled == 0


def test_same_minute_entry_and_extension_cancels() -> None:
    trades, counts = simulate([LONG], bars((115, 121, 109, 112)), NO_COST)
    assert trades == [] and counts.cancelled_extension == 1


def test_stop_in_fill_minute_and_stop_before_target() -> None:
    (t,), _ = simulate([LONG], bars((115, 116, 99, 101)), NO_COST)
    assert t.reason == "STOP" and t.r_gross == pytest.approx(-1.0)
    (t,), _ = simulate([LONG], bars((115, 116, 109, 112), (112, 150, 99, 120)), NO_COST)
    assert t.reason == "STOP"


def test_gap_through_stop_fills_at_open() -> None:
    (t,), _ = simulate([LONG], bars((115, 116, 109, 112), (95, 96, 90, 92)), NO_COST)
    assert t.reason == "STOP" and t.exit_price == 95 and t.r_gross == pytest.approx(-1.5)


def test_time_exit_and_expiry() -> None:
    rows = [(115, 116, 109, 112)] + [(112, 113, 111, 112)] * 25
    (t,), _ = simulate([LONG], bars(*rows), NO_COST)
    assert t.reason == "TIME" and t.r_gross == pytest.approx(0.2)
    trades, counts = simulate([LONG], bars(*[(115, 116, 111, 115)] * 12), NO_COST)
    assert trades == [] and counts.expired == 1


def test_short_mirror() -> None:
    (t,), _ = simulate([SHORT], bars((105, 111, 104, 108), (108, 109, 70, 75)), NO_COST)
    assert t.reason == "TARGET" and t.r_gross == pytest.approx(3.236)


def test_replace_pending_and_ignore_while_holding() -> None:
    later = Setup(as_of_ms=2 * M, direction=1, entry=200.0, stop=190.0, target=230.0, b=210.0)
    _, counts = simulate(
        [LONG, later],
        bars((115, 116, 111, 115), (115, 116, 111, 115), (115, 116, 111, 115)),
        NO_COST,
    )
    assert counts.replaced == 1
    _, counts = simulate(
        [LONG, later],
        bars((115, 116, 109, 112), (112, 113, 111, 112), (112, 113, 111, 112)),
        NO_COST,
    )
    assert counts.ignored_in_position == 1


def test_costs_reduce_r() -> None:
    (t,), _ = simulate(
        [LONG], bars((115, 116, 109, 112), (112, 150, 111, 145)), Rules(cost_side=0.001)
    )
    assert t.r_net == pytest.approx(3.236 - 0.001 * (110 + 142.36) / 10)


def test_skipped_setup_never_trades() -> None:
    skipped = Setup(0, 1, 110.0, 100.0, 142.36, 120.0, skipped=True)
    trades, counts = simulate([skipped], bars((115, 116, 109, 112)), NO_COST)
    assert trades == [] and counts.skipped == 1
