from __future__ import annotations

import pytest
from scripts.run_lt_kr import KrPanel
from scripts.run_more_152 import (
    hfea_equity,
    lagged_equity,
    lev,
    lowvol_pick,
    sma,
    volatility,
)


def test_lagged_equity_acts_next_close_and_charges_each_switch() -> None:
    rets = [0.0, 0.1, 0.1, 0.1]
    on = [True, True, False, False]
    eq = lagged_equity(rets, on, 0.01)
    # t1 종가에 들어감(비용) → t2 수익 → t3 수익 뒤 나옴(비용)
    assert eq[-1] == pytest.approx(0.99 * 1.1 * 1.1 * 0.99)


def test_leverage_subtracts_funding_on_borrowed_part_and_fee() -> None:
    assert lev(0.01, 3, 0.0252, 0.0252) == pytest.approx(0.03 - 2 * 0.0001 - 0.0001)
    assert lev(0.01, 1, 0.05, 0.0) == pytest.approx(0.01)


def test_sma_window() -> None:
    assert sma([1.0, 2.0, 3.0, 4.0], 2) == [None, 1.5, 2.5, 3.5]


def test_hfea_rebalances_on_first_day_of_quarter_only() -> None:
    days = ["2020-03-30", "2020-03-31", "2020-04-01", "2020-04-02"]
    spy = [100.0, 110.0, 110.0, 110.0]  # 3 배 → +30%
    tlt = [100.0, 100.0, 100.0, 100.0]
    eq = hfea_equity(spy, tlt, [0.0] * 4, days, 0.0)
    assert eq[1] == pytest.approx(0.55 * 1.3 + 0.45 - 4 * 0.0091 / 252, abs=1e-3)
    assert eq[2] == pytest.approx(eq[3], rel=1e-3)  # 4 월 첫날 맞춘 뒤 가격 그대로


def _panel(series: dict[str, list[float | None]]) -> KrPanel:
    n = len(next(iter(series.values())))
    days = [f"d{i:04d}" for i in range(n)]
    return KrPanel(
        days,
        series,
        {s: [1.0] * n for s in series},
        {},
        dict.fromkeys(series, 0),
        dict.fromkeys(series, n - 1),
    )


def test_lowvol_picks_calmest_and_skips_cheap_or_short_history() -> None:
    n = 260
    calm: list[float | None] = [2000.0 * (1.001 if i % 2 else 0.999) for i in range(n)]
    wild: list[float | None] = [2000.0 * (1.05 if i % 2 else 0.95) for i in range(n)]
    cheap: list[float | None] = [500.0] * n  # 변동 0 이지만 1,000 원 미만
    short: list[float | None] = [None] * 100 + [3000.0] * (n - 100)
    p = _panel({"C": calm, "W": wild, "P": cheap, "S": short})
    assert volatility(p.close["S"], n - 1) is None
    assert lowvol_pick(p, n - 1, ["W", "C", "P", "S"]) == ["C", "W"]
