"""문서 286 — 범위 맞추기 모의 계좌 · 10 년 창 · 판정 (순수 함수)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import pytest
from scripts.run_brake_184 import simulate

from scripts import run_band_286 as b
from scripts import run_gold_silver_285 as g


def test_rebalance_ok() -> None:
    assert b.rebalance_ok(65, 60, 100, None)
    assert not b.rebalance_ok(65, 60, 100, 0.10)
    assert b.rebalance_ok(71, 60, 100, 0.10) and b.rebalance_ok(49, 60, 100, 0.10)


def test_months() -> None:
    assert b.window_starts("2015-11", "2016-02") == ["2015-11", "2015-12", "2016-01", "2016-02"]
    assert b.add_months("2016-08", 120) == "2026-08" and b.add_months("2015-12", 1) == "2016-01"


def test_window_summary() -> None:
    w = b.window_summary([(2.0, 2.1), (3.0, 2.9), (1.5, 1.5), (4.0, 4.4)])
    assert w["windows"] == 4 and w["band_ge_share"] == 0.75
    assert w["worst_diff"] == pytest.approx(-0.1) and w["min"] == {"M": 1.5, "B": 1.5}


def _res(share: float, med: tuple[float, float], s: tuple[float, float], b5: float) -> Any:
    return {
        "windows": {"S2": {"band_ge_share": share, "median": {"M": med[0], "B": med[1]}}},
        "S": {"M": {"irr_liq": 0.20, "mdd": 0.50}, "B10": {"irr_liq": s[0], "mdd": s[1]}},
        "S2": {k: {"irr_liq": v} for k, v in (("M", 0.21), ("B5", b5), ("B15", 0.22))},
    }


def test_judge() -> None:
    assert b.judge(_res(0.7, (2.8, 2.9), (0.21, 0.51), 0.215))["status"] == "채택 후보"
    assert b.judge(_res(0.7, (2.8, 2.9), (0.21, 0.53), 0.215))["status"] == "불명"
    assert b.judge(_res(0.7, (2.8, 2.9), (0.21, 0.51), 0.20))["status"] == "불명"
    assert b.judge(_res(0.35, (2.8, 2.9), (0.21, 0.51), 0.215))["status"] == "불리"
    assert b.judge(_res(0.7, (2.8, 2.9), (0.194, 0.51), 0.215))["status"] == "불리"


def _data(n: int = 900) -> dict[str, Any]:
    d0 = date(2010, 1, 4)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    lev = {d: 50 * (1 + 0.6 * math.sin(i / 40)) + i * 0.08 for i, d in enumerate(days)}
    div = {d: 30 + i * 0.01 + math.sin(i / 7) for i, d in enumerate(days)}
    vol = {d: 0.15 + 0.15 * abs(math.sin(i / 30)) for i, d in enumerate(days)}
    trend = {d: math.cos(i / 40) > 0 for i, d in enumerate(days)}
    return {"days": days, "lev": lev, "div": div, "vol": vol, "trend": trend,
            "start": days[1], "monthly_krw": 300_000.0}  # fmt: skip


def test_monthly_equals_184_and_285() -> None:
    data = _data()
    gold = {d: 100 + 10 * math.cos(i / 50) for i, d in enumerate(data["days"])}
    a = simulate(data, data["div"], ref="T", park="cash")
    m = b.simulate_band(data, data["div"])
    assert m["navs"] == pytest.approx(a["navs"], rel=1e-12)
    assert m["after_tax_liq"] == pytest.approx(a["after_tax_liq"], rel=1e-12)
    ref = g.simulate_mix(data, data["div"], {"G": gold})
    mg = b.simulate_band(data, data["div"], gold)
    assert mg["after_tax_liq"] == pytest.approx(ref["after_tax_liq"], rel=1e-12)


def test_band_sells_less() -> None:
    data = _data()
    m = b.simulate_band(data, data["div"])
    w = b.simulate_band(data, data["div"], band=0.10)
    assert w["rebal_months"] < m["rebal_months"] and w["all_sells"] < m["all_sells"]
    assert len(w["sales"]) == len(m["sales"]) or len(w["sales"]) > 0  # 브레이크는 그대로 돈다
