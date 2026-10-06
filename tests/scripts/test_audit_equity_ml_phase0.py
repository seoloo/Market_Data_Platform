from __future__ import annotations

from scripts.audit_equity_ml_phase0 import months_between, power_table


def test_months_between_inclusive() -> None:
    assert months_between("2018-04", "2026-07") == 100
    assert months_between("2020-01", "2020-01") == 1


def test_power_table_needed_months_shrink_with_effect_and_grow_with_sd() -> None:
    rows = power_table([0.01, 0.03], 88)
    small, big = rows
    assert small["needed_months"]["3%/yr"] > small["needed_months"]["10%/yr"]
    assert big["needed_months"]["5%/yr"] > small["needed_months"]["5%/yr"]
    assert big["mde_annual_arith_at_available"] > small["mde_annual_arith_at_available"]
