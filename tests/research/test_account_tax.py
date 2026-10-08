from __future__ import annotations

import pytest

from auto_trading.research.account_tax import (
    GENERAL,
    ISA,
    TAXABLE,
    Account,
    simulate,
    tracking_gap,
)


def _flat(n: int, r: float) -> tuple[list[str], list[dict[str, float]], list[dict[str, float]]]:
    months = [f"{2000 + k // 12}-{k % 12 + 1:02d}" for k in range(n)]
    return months, [{"A": r} for _ in months], [{} for _ in months]


def test_isa_single_asset_lump_tax_at_end() -> None:
    months, rets, inc = _flat(12, 0.05)
    acct = Account("I", 0.0, 0.099, 2_000_000.0, 0.0)
    out = simulate(acct, months, rets, inc, {"A": 1.0}, [10_000_000.0] + [0.0] * 11)
    gross = 10_000_000.0 * 1.05**12
    assert out["final"] == pytest.approx(gross - 0.099 * (gross - 10_000_000.0 - 2_000_000.0))


def test_taxable_annual_deduction_and_year_end_payment() -> None:
    months, rets, inc = _flat(24, 0.02)
    acct = Account("T", 0.0, 0.22, 2_500_000.0, 0.0)
    out = simulate(acct, months, rets, inc, {"A": 1.0}, [10_000_000.0] + [0.0] * 23)
    # 한 자산 · 매도 없음 → 중간 세금 없음, 끝에 한 번
    gross = 10_000_000.0 * 1.02**24
    assert out["final"] == pytest.approx(gross - 0.22 * (gross - 10_000_000.0 - 2_500_000.0))


def test_rebalance_sale_realizes_gain_in_taxable_not_isa() -> None:
    months = ["2000-11", "2000-12", "2001-01"]
    rets = [{"A": 1.0, "B": 0.0}, {"A": 0.0, "B": 0.0}, {"A": 0.0, "B": 0.0}]
    inc: list[dict[str, float]] = [{}, {}, {}]
    w = {"A": 0.5, "B": 0.5}
    c = [20_000_000.0, 0.0, 0.0]
    t = simulate(Account("T", 0.0, 0.22, 2_500_000.0, 0.0), months, rets, inc, w, c)
    i = simulate(Account("I", 0.0, 0.099, 2_000_000.0, 0.0), months, rets, inc, w, c)
    # 12 월 초 A 5 백만 팔아 차익 2.5 백만 → 공제와 같아 세금 0; 끝에 남은 차익 7.5 백만 과세
    assert t["final"] == pytest.approx(30_000_000.0 - 0.22 * (7_500_000.0 - 2_500_000.0))
    assert i["final"] == pytest.approx(30_000_000.0 - 0.099 * (10_000_000.0 - 2_000_000.0))


def test_general_taxes_each_sale_and_distributions() -> None:
    months = ["2000-01", "2000-02"]
    rets = [{"A": 0.10}, {"A": 0.0}]
    inc = [{"A": 0.10}, {}]
    out = simulate(GENERAL, months, rets, inc, {"A": 1.0}, [1_000_000.0, 0.0])
    assert out["taxes"] > 0
    assert out["final"] < 1_100_000.0
    assert TAXABLE.kind == "T" and ISA.kind == "I"


def test_tracking_gap_zero_when_identical() -> None:
    kr = {"2020-01": 100.0, "2020-02": 110.0, "2020-03": 121.0, "2020-04": 133.1}
    us = {"2020-01": 10.0, "2020-02": 11.0, "2020-03": 12.1, "2020-04": 13.31}
    fx = dict.fromkeys(kr, 1000.0)
    g = tracking_gap(kr, us, fx)
    assert g["months"] == 2
    assert g["gap_annual"] == pytest.approx(0.0, abs=1e-12)
