from __future__ import annotations

import math

from scripts.demo_grid_leverage import GA, simulate_lev_grid

from auto_trading.backtest.grid import DAY_MS, MinuteBars, simulate_grid

MIN = 60_000


def _bars(prices: list[float], per_day: int = 240) -> MinuteBars:
    """하루 ``per_day`` 분, 분마다 앞뒤 가격 사이를 오가는 봉."""
    t, o, h, lo, c = [], [], [], [], []
    for i, p in enumerate(prices):
        q = prices[i - 1] if i else p
        t.append(i * (DAY_MS // per_day))
        o.append(q)
        c.append(p)
        h.append(max(p, q) * 1.0005)
        lo.append(min(p, q) * 0.9995)
    return MinuteBars(t, o, h, lo, c)


def _wave(days: int, per_day: int = 240, drift: float = 0.0) -> list[float]:
    n = days * per_day
    return [100 * (1 + drift) ** (i / per_day) * (1 + 0.03 * math.sin(i / 37)) for i in range(n)]


def test_one_x_matches_137_grid() -> None:
    bars = _bars(_wave(6))
    sigma = {d * DAY_MS: 0.02 for d in range(6)}
    ref = simulate_grid(bars, {}, sigma, from_ms=0, to_ms=5 * DAY_MS, initial=10_000.0, cfg=GA)
    mine = simulate_lev_grid(
        bars, sigma, {}, from_ms=0, to_ms=5 * DAY_MS, initial=10_000.0, leverage=1.0
    )
    assert ref.round_trips > 0
    assert mine.round_trips == ref.round_trips
    assert all(
        math.isclose(a, b, rel_tol=1e-12) for a, b in zip(mine.equity, ref.equity, strict=True)
    )
    assert mine.realized_by_day.keys() == ref.realized_by_day.keys()
    assert all(
        math.isclose(mine.realized_by_day[d], ref.realized_by_day[d], rel_tol=1e-9)
        for d in ref.realized_by_day
    )


def test_crash_liquidates_ten_x_not_one_x() -> None:
    prices = _wave(2) + [100 * (1 - 0.35 * i / 480) for i in range(480)] + [65.0] * 480
    bars = _bars(prices)
    sigma = {d * DAY_MS: 0.02 for d in range(6)}
    ten = simulate_lev_grid(
        bars, sigma, {}, from_ms=0, to_ms=5 * DAY_MS, initial=10_000.0, leverage=10.0
    )
    one = simulate_lev_grid(
        bars, sigma, {}, from_ms=0, to_ms=5 * DAY_MS, initial=10_000.0, leverage=1.0
    )
    assert ten.liquidated_ms is not None
    assert ten.equity[-1] == 0.0
    assert one.liquidated_ms is None and one.equity[-1] > 0
    assert min(v for v in ten.realized_by_day.values()) >= 0  # 화면 '오늘 수익'은 음수가 없다


def test_funding_is_paid_on_long_notional() -> None:
    bars = _bars(_wave(3))
    sigma = {d * DAY_MS: 0.02 for d in range(3)}
    fund = dict.fromkeys(range(0, 3 * DAY_MS, 8 * 3600000), 0.001)
    with_f = simulate_lev_grid(
        bars, sigma, fund, from_ms=0, to_ms=2 * DAY_MS, initial=10_000.0, leverage=3.0
    )
    without = simulate_lev_grid(
        bars, sigma, {}, from_ms=0, to_ms=2 * DAY_MS, initial=10_000.0, leverage=3.0
    )
    assert with_f.funding > 0
    assert with_f.equity[-1] < without.equity[-1]
