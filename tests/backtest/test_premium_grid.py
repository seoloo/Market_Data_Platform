"""문서 138 — USDT 프리미엄 그리드: 중심·호가 반올림·체결·같은 분·손절 최악·대기·그냥 그리드."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest

from auto_trading.backtest.grid import MinuteBars
from auto_trading.backtest.premium_grid import (
    PremiumGridConfig,
    PremiumGridResult,
    centers,
    simulate_premium_grid,
)

D0 = 1_720_396_800_000  # 2024-07-08 00:00 UTC
DAY = 86_400_000
M = 60_000
CFG = PremiumGridConfig(levels=4, step=0.01, fee=0.001, stop_fee=0.001)
Row = tuple[int, float, float, float, float]


def bars(rows: Sequence[Row]) -> MinuteBars:
    return MinuteBars(
        [r[0] for r in rows],
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
        [r[4] for r in rows],
    )


def run(
    rows: Sequence[Row], center: float = 1000.0, days: int = 1, cfg: PremiumGridConfig = CFG
) -> PremiumGridResult:
    cmap = {D0 + k * DAY: center for k in range(days)}
    return simulate_premium_grid(
        bars(rows), cmap, from_ms=D0, to_ms=D0 + (days - 1) * DAY, initial=1000.0, cfg=cfg
    )


def test_center_uses_previous_fx_and_30_day_premium() -> None:
    rows = [(D0 + k * DAY, 1100, 1100, 1100, 1100) for k in range(31)]
    fx = {"2024-07-08": 1000.0, "2024-07-20": 1050.0}
    cmap, prem = centers(bars(rows), fx, avg_days=30)
    first = D0 + 30 * DAY  # 0~29 일 30 개 P 다음 날
    assert first in cmap and D0 + 29 * DAY not in cmap
    mean_p = sum(prem[D0 + k * DAY] for k in range(30)) / 30
    assert cmap[first] == pytest.approx(1050.0 * (1 + mean_p))
    assert prem[D0] == pytest.approx(0.1)


def test_buy_floor_tick_sell_ceil_tick_round_trip() -> None:
    rows = [
        (D0, 1000, 1000, 1000, 1000),
        (D0 + M, 995, 995, 989, 990),
        (D0 + 2 * M, 995, 1001, 994, 1000),
    ]
    r = run(rows)
    # 칸 1 = 1000 × 0.99 = 990 (내림), 매도 목표 = 990 × 1.01 = 999.9 → 올림 1000
    assert (r.buys, r.round_trips) == (1, 1)
    size = 250.0
    assert r.trip_pnl[0] == pytest.approx(size / 990 * 1000 * 0.999 - size * 1.001)


def test_touch_is_not_fill_and_same_minute_rule() -> None:
    r = run([(D0, 1000, 1000, 1000, 1000), (D0 + M, 995, 995, 990, 991)])  # 저가 = 990 정확히
    assert r.buys == 0
    r2 = run([(D0, 1000, 1000, 1000, 1000), (D0 + M, 995, 1005, 989, 1000)])
    assert (r2.buys, r2.round_trips) == (1, 0)


def test_through_reference_needs_two_won() -> None:
    rows = [(D0, 1000, 1000, 1000, 1000), (D0 + M, 995, 995, 989, 990)]
    assert run(rows, cfg=replace(CFG, through=1.0)).buys == 0
    rows2 = [(D0, 1000, 1000, 1000, 1000), (D0 + M, 995, 995, 988, 990)]
    assert run(rows2, cfg=replace(CFG, through=1.0)).buys == 1


def test_stop_worst_case_and_blocked_until_next_day() -> None:
    # 손절선 = 1000 × (1 − 5 × 0.01) = 950, 시가 955 · 저가 948
    rows = [
        (D0, 1000, 1000, 1000, 1000),
        (D0 + M, 955, 956, 948, 950),
        (D0 + 2 * M, 950, 950, 940, 945),
    ]
    r = run(rows)
    assert r.stops == 1 and r.buys == 4
    assert r.stop_pnl[0] < 0
    # 다음 분 더 떨어져도 그날은 다시 사지 않는다
    assert r.buys == 4


def test_plain_grid_recenters_up_only_without_stop() -> None:
    cfg = replace(CFG, recenter_daily=False, use_stop=False)
    rows = [
        (D0, 1000, 1000, 1000, 1000),
        (D0 + M, 1011, 1012, 1010, 1011),
        (D0 + 2 * M, 900, 900, 890, 895),
    ]
    r = simulate_premium_grid(bars(rows), {}, from_ms=D0, to_ms=D0, initial=1000.0, cfg=cfg)
    assert r.recenters == 1 and r.stops == 0 and r.buys == 4


def test_no_center_no_trading() -> None:
    rows = [(D0, 1000, 1000, 1000, 1000), (D0 + M, 900, 900, 800, 850)]
    r = simulate_premium_grid(bars(rows), {}, from_ms=D0, to_ms=D0, initial=1000.0, cfg=CFG)
    assert r.buys == 0 and r.equity[-1] == 1000.0
