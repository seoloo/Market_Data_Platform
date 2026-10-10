from __future__ import annotations

from datetime import date, timedelta

import pytest
import scripts.run_brake_184 as m184
from scripts.run_brake_183 import signals
from scripts.run_glide_isa_198 import dragged, simulate_split


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2020, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _data(lev_step: float, div_step: float, n: int = 700) -> dict[str, object]:
    days = _days(n)
    lev = {d: 100 * (1 + lev_step) ** i * (1.02 if i % 3 else 0.99) for i, d in enumerate(days)}
    div = {d: 50 * (1 + div_step) ** i for i, d in enumerate(days)}
    _, trend = signals(days, lev)
    return {
        "days": days,
        "lev": lev,
        "div": div,
        "vol": dict.fromkeys(days, 0.25),
        "trend": trend,
        "rf": dict.fromkeys(days, 2.0),
        "start": days[130],
    }


def test_dragged_compounds_yearly_drag() -> None:
    days = _days(253)
    out = dragged(days, dict.fromkeys(days, 100.0), 0.01)
    assert out[days[0]] == 100.0 and out[days[252]] == pytest.approx(99.0, rel=1e-9)


def test_split_equals_single_account_when_nothing_gains() -> None:
    # 값이 내리기만 하면 세금이 없다 → 계좌를 나눠도 매매가 같아 끝 가치가 같아야 한다
    data = _data(-0.0008, -0.0003)
    a = m184.simulate(data, data["lev"], ref="T", park="cash", monthly_krw=300_000.0)  # type: ignore[arg-type]
    s = simulate_split(data, data["div"], monthly_krw=300_000.0, withdraw=True, term_years=None)  # type: ignore[arg-type]
    assert s["isa_tax"] == 0 and s["toss_tax"] == 0
    assert s["after_tax_liq"] == pytest.approx(a["after_tax_liq"], rel=1e-9)


def test_isa_tax_and_no_withdraw_mode() -> None:
    data = _data(0.0015, 0.0008)
    f = simulate_split(data, data["div"], monthly_krw=1_000_000.0, withdraw=True, term_years=3)  # type: ignore[arg-type]
    c = simulate_split(data, data["div"], monthly_krw=1_000_000.0, withdraw=False, term_years=None)  # type: ignore[arg-type]
    assert f["isa_tax"] >= 0 and f["dep_total"] > 0
    assert c["over_withdraw_months"] == 0
    assert c["contrib"] == f["contrib"]
