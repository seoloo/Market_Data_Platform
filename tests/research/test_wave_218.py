"""문서 218 — 이평선 · 스토캐스틱 · 쌍바닥 · 상태 · 묶음 부트스트랩 검산."""

from __future__ import annotations

import math

from auto_trading.research.wave_218 import (
    MonthSums,
    bootstrap_excess,
    find_bottoms,
    slow_k,
    sma,
    states,
)


def test_sma_matches_manual() -> None:
    out = sma([1.0, 2.0, 3.0, 4.0], 2)
    assert math.isnan(out[0])
    assert out[1:] == [1.5, 2.5, 3.5]


def test_slow_k_range_and_flat() -> None:
    c = [10.0, 11, 12, 13, 14, 15, 16, 17]
    k = slow_k([x + 0.5 for x in c], [x - 0.5 for x in c], c)
    assert all(math.isnan(v) for v in k[:6])
    assert 0 <= k[-1] <= 100
    flat = [1.0] * 10
    assert slow_k(flat, flat, flat)[-1] == 50.0


def test_find_bottoms_lower_and_higher_and_triple() -> None:
    k = [60.0, 20, 50, 70, 40, 10, 40, 80, 50, 25, 60, 80]
    got = find_bottoms(k)
    # 바닥 j=1(20) · 5(10) · 9(25). (1,5) 저점하락, (5,9) 저점상승 + 앞도 쌍바닥 → 쓰리바닥
    assert [(b.t, b.higher, b.triple) for b in got] == [(6, False, False), (10, True, True)]


def test_find_bottoms_needs_bounce_and_gap() -> None:
    no_bounce = [60.0, 20, 25, 30, 25, 15, 50]
    assert find_bottoms(no_bounce) == []
    too_close = [60.0, 20, 60, 18, 60]
    assert find_bottoms(too_close) == []
    too_high = [60.0, 40, 70, 90, 50, 35, 60]
    assert find_bottoms(too_high) == []


def test_states_bull_bear() -> None:
    up = [10 + 90 * i / 299 for i in range(300)]
    assert states(up)[-1] == "A"
    assert states(up)[100] == ""  # 240 일선 전
    assert states(up[::-1])[-1] == "C"
    dip = list(up)
    dip[-1] = (sma(up, 20)[-2] + sma(up, 60)[-2]) / 2
    assert states(dip)[-1] == "B"


def test_bootstrap_excess_point_and_ci() -> None:
    s = MonthSums()
    for m in range(24):
        month = f"2020-{m:02d}"
        s.add("sig", month, 0.02)
        s.add("base", month, 0.0)
        s.add("base", month, 0.01)
    point, lo, hi = bootstrap_excess(s, "sig", "base", reps=200)
    assert abs(point - 0.015) < 1e-12
    assert lo - 1e-12 <= point <= hi + 1e-12
    assert math.isnan(bootstrap_excess(s, "none", "base")[0])
