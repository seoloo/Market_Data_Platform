from __future__ import annotations

import math
from array import array

from scripts.run_basis_grid import Series, simulate

DAY = 86_400_000
MIN = 60_000


def _series(basis: list[float], spot: float = 100.0) -> Series:
    t = array("q", (i * MIN for i in range(len(basis))))
    return Series(t, array("d", [spot] * len(basis)), array("d", [spot * (1 + b) for b in basis]))


def test_round_trip_earns_step_minus_costs_with_next_minute_fill() -> None:
    # 0 → 0.4% (칸 1 열림 신호) → 0.4% (체결) → 0 (닫힘 신호) → 0 (체결)
    s = _series([0.0, 0.004, 0.004, 0.0, 0.0])
    r = simulate(s, {}, step=0.003, spot_fee=0.001, perp_fee=0.0002, levels=5)
    assert r.round_trips == 1
    q = 1.0 / 100.0
    expected = q * 100 * 0.004 - (q * 100 * 0.001 + q * 100.4 * 0.0002) - (q * 100 * 0.0012)
    assert math.isclose(r.trade_pnl, expected, rel_tol=1e-9)


def test_same_minute_fill_would_capture_noise_delay_does_not() -> None:
    # 한 분만 튀고 바로 돌아오는 베이시스 — 다음 분 체결이면 이미 사라졌다
    s = _series([0.0, 0.004, 0.0, 0.0, 0.0])
    delayed = simulate(s, {}, step=0.003, spot_fee=0.0, perp_fee=0.0)
    instant = simulate(s, {}, step=0.003, spot_fee=0.0, perp_fee=0.0, delay=0)
    assert instant.trade_pnl > 0 >= delayed.trade_pnl


def test_negative_basis_never_opens_and_funding_paid_while_open() -> None:
    s = _series([-0.004] * 3)
    assert simulate(s, {}, step=0.003, spot_fee=0.001, perp_fee=0.0002).round_trips == 0
    s = _series([0.0, 0.004, 0.004, 0.004, 0.004])
    r = simulate(s, {3 * MIN: 0.001}, step=0.003, spot_fee=0.0, perp_fee=0.0)
    assert math.isclose(r.funding, 0.01 * 100.4 * 0.001, rel_tol=1e-9)  # 칸 1 하나 × 펀딩


def test_levels_stack_and_unwind_one_step_below() -> None:
    s = _series([0.0, 0.0065, 0.0065, 0.0035, 0.0035, 0.0, 0.0])
    r = simulate(s, {}, step=0.003, spot_fee=0.0, perp_fee=0.0)
    assert r.round_trips == 2  # 칸 1·2 열림 → 0.35% 에서 칸 2 닫힘 → 0 에서 칸 1 닫힘
