from __future__ import annotations

import pytest

from auto_trading.ops.mf_paper import (
    corr,
    judge,
    mark,
    monthly_returns,
    new_month,
    open_book,
    rebalance,
    value,
)

T = {"E": 0.8, "S": 0.2}


def test_open_mark_and_rebalance_costs() -> None:
    p0 = {"E": 10.0, "S": 20.0}
    b = open_book(1000.0, T, p0, 0.0015)
    assert value(b, p0) == pytest.approx(1000 * (1 - 0.0015))
    p1 = {"E": 20.0, "S": 20.0}  # 주식 두 배 → 비중 표류
    mark(b, "2026-10-09", p1)
    v = value(b, p1)
    fee = rebalance(b, T, p1, 0.0015)
    assert fee > 0
    assert value(b, p1) == pytest.approx(v - fee)
    assert b.units["E"] * p1["E"] / value(b, p1) == pytest.approx(0.8)


def test_mark_tracks_drawdown() -> None:
    b = open_book(100.0, {"E": 1.0}, {"E": 1.0}, 0.0)
    for d, px in (("a", 1.0), ("b", 1.2), ("c", 0.9)):
        mark(b, d, {"E": px})
    assert b.mdd == pytest.approx(0.25)


def test_new_month_and_monthly_returns() -> None:
    assert new_month("2026-10-30", "2026-11-02")
    assert not new_month(None, "2026-11-02")
    m = monthly_returns([("2026-10-08", 100.0), ("2026-10-30", 110.0), ("2026-11-30", 99.0)])
    assert m["2026-10"] == pytest.approx(0.10)
    assert m["2026-11"] == pytest.approx(-0.10)


def test_judge_rules() -> None:
    months = [f"2027-{k:02d}" for k in range(1, 13)]
    sleeve = {m: (0.02 if k % 2 else -0.01) for k, m in enumerate(months)}
    equity = {m: (0.01 if k % 3 else -0.02) for k, m in enumerate(months)}
    aqr = dict(sleeve)
    mix = {m: 0.8 * equity[m] + 0.2 * sleeve[m] for m in months}
    r = judge(sleeve, equity, aqr, 0.10, 0.15, mix, equity)
    assert r["J2"] is True and r["J2_sleeve_aqr_corr"] == pytest.approx(1.0)
    assert corr([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert corr([1, 1, 1], [1, 2, 3]) is None
