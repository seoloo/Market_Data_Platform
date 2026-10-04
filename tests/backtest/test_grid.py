"""문서 137 — 추세 켜짐 그리드: 체결·같은 분 금지·손절 최악 가정·올리기만·꺼짐 정리·대기."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest

from auto_trading.backtest.grid import (
    DAY_MS,
    GridConfig,
    GridResult,
    MinuteBars,
    daily_closes,
    hold_trend,
    simulate_grid,
    trend_and_sigma,
)

D0 = 1_577_836_800_000  # 2020-01-01 00:00 UTC
M = 60_000
CFG = GridConfig(grid_fee=0.001, stop_fee=0.002, exit_fee=0.001)
SIGMA = 0.02  # s = 0.5 × 0.02 = 1%


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
    rows: Sequence[Row],
    trend: Mapping[int, bool] | None = None,
    days: int = 1,
    cfg: GridConfig = CFG,
) -> GridResult:
    trend = trend if trend is not None else {D0 + k * DAY_MS: True for k in range(days)}
    sigma = {D0 + k * DAY_MS: SIGMA for k in range(days)}
    return simulate_grid(
        bars(rows),
        trend,
        sigma,
        from_ms=D0,
        to_ms=D0 + (days - 1) * DAY_MS,
        initial=1000.0,
        cfg=cfg,
    )


def test_one_round_trip_counts_profit_after_fees() -> None:
    r = run(
        [
            (D0, 100, 100, 100, 100),
            (D0 + M, 99.5, 99.5, 98.9, 99.2),
            (D0 + 2 * M, 99.5, 100.1, 99.4, 100),
        ]
    )
    assert (r.buys, r.round_trips) == (1, 1)
    size = 100.0
    expected = size / 99 * 99.99 * (1 - 0.001) - size * 1.001
    assert r.realized_grid == pytest.approx(expected)
    assert r.equity[-1] == pytest.approx(1000 + expected)


def test_no_sell_in_the_buying_minute_and_touch_is_not_a_fill() -> None:
    r = run([(D0, 100, 100, 100, 100), (D0 + M, 99.5, 101, 98.9, 100)])
    assert (r.buys, r.round_trips) == (1, 0)
    r2 = run([(D0, 100, 100, 100, 100), (D0 + M, 99.5, 99.5, 99.0, 99.2)])  # 정확히 99 = 미체결
    assert r2.buys == 0


def test_stop_buys_crossed_levels_then_sells_at_worse_of_stop_and_open() -> None:
    rows = [(D0, 100, 100, 100, 100), (D0 + M, 88.5, 88.6, 88.0, 88.2)]  # 갭: 시가 88.5 < 손절 89
    r = run(rows)
    assert r.stops == 1 and r.buys == 10
    last = (1000 - 9 * 100 * 1.001) / 1.001  # 비용 때문에 마지막 칸은 남은 현금만큼
    notional = [100.0] * 9 + [last]
    cost = sum(x * 1.001 for x in notional)
    proceeds = sum(
        x / (100 * (1 - k * 0.01)) * 88.5 * (1 - 0.002) for k, x in enumerate(notional, start=1)
    )
    assert r.stop_pnl[0] == pytest.approx(proceeds - cost)


def test_cooldown_blocks_new_grid_for_seven_days() -> None:
    rows = [(D0, 100, 100, 100, 100), (D0 + M, 88.5, 88.6, 88.0, 88.2)]
    rows += [(D0 + k * DAY_MS, 90, 90, 90, 90) for k in range(1, 9)]
    r = run(rows, days=9)
    assert r.activations == 2  # 첫날 + 7 일 뒤(8 일째 시작)
    assert r.stops == 1


def test_grid_moves_up_only() -> None:
    rows = [
        (D0, 100, 100, 100, 100),
        (D0 + M, 101, 101.5, 100.5, 101.2),  # 보유 0, 종가 ≥ 101 → 중심 101.2
        (D0 + 2 * M, 101, 101, 100.0, 100.1),  # 새 1 칸 = 101.2 × 0.99 = 100.188 아래 → 매수
        (D0 + 3 * M, 99, 99, 97, 97.5),  # 내려가도 중심은 그대로
    ]
    r = run(rows)
    assert r.recenters == 1
    assert r.buys >= 1


def test_trend_off_sells_everything_at_next_day_open() -> None:
    rows = [
        (D0, 100, 100, 100, 100),
        (D0 + M, 99.5, 99.5, 98.9, 99.0),
        (D0 + DAY_MS, 98, 98, 98, 98),
    ]
    trend = {D0: True, D0 + DAY_MS: False}
    r = run(rows, trend=trend, days=2)
    assert r.trend_exits == 1
    assert r.exit_pnl[0] == pytest.approx(100 / 99 * 98 * (1 - 0.001) - 100 * 1.001)
    assert r.exposure[-1] == 0.0


def test_grid_alone_never_stops() -> None:
    rows = [(D0, 100, 100, 100, 100), (D0 + M, 80, 80, 79, 79.5)]
    r = run(rows, cfg=replace(CFG, use_trend=False, use_stop=False))
    assert r.stops == 0 and r.buys == 10


def test_hold_trend_and_buy_hold() -> None:
    rows = [
        (D0, 100, 100, 100, 100),
        (D0 + DAY_MS, 110, 110, 110, 110),
        (D0 + 2 * DAY_MS, 120, 120, 120, 120),
    ]
    th = hold_trend(
        bars(rows),
        {D0: True, D0 + DAY_MS: False},
        from_ms=D0,
        to_ms=D0 + 2 * DAY_MS,
        initial=1000.0,
        fee=0.001,
    )
    assert th.equity[-1] == pytest.approx(1000 / 1.001 / 100 * 110 * 0.999)
    bh = hold_trend(bars(rows), None, from_ms=D0, to_ms=D0 + 2 * DAY_MS, initial=1000.0, fee=0.001)
    assert bh.equity[-1] == pytest.approx(1000 / 1.001 / 100 * 120)


def test_trend_uses_previous_close_and_sigma_window() -> None:
    closes = {D0 + k * DAY_MS: 100 * 1.01**k for k in range(130)}
    trend, sigma = trend_and_sigma(closes, CFG)
    first = D0 + 121 * DAY_MS  # 121 개 종가(0~120)가 쌓인 다음 날
    assert trend[first] is True and D0 + 120 * DAY_MS not in trend
    assert sigma[D0 + 21 * DAY_MS] == pytest.approx(0.0, abs=1e-12)


def test_daily_closes_take_last_minute() -> None:
    b = bars([(D0, 1, 1, 1, 1), (D0 + M, 2, 2, 2, 2), (D0 + DAY_MS, 3, 3, 3, 3)])
    assert daily_closes(b) == {D0: 2, D0 + DAY_MS: 3}
