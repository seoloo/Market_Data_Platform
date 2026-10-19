"""문서 285 — 금은비 바꿔 들기 · Deneb 금 몫 모의 계좌 (순수 함수)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import pytest
from scripts.run_brake_184 import simulate

from scripts import run_gold_silver_285 as g


def test_switch_states_hysteresis() -> None:
    assert g.switch_states([70, 81, 75, 66, 65, 79, 80]) == ["G", "S", "S", "S", "G", "G", "S"]
    assert g.switch_states([85, 60], lo=50) == ["S", "S"]


def test_lagged() -> None:
    assert g.lagged(["S", "S", "G", "S"]) == ["G", "G", "S", "S"]
    assert g.lagged(["S", "G"], 1) == ["G", "S"]


def test_metal_path_switch_cost_and_fee() -> None:
    gold, silver = [100.0, 100.0, 100.0], [10.0, 10.0, 11.0]
    eq = g.metal_path(gold, silver, ["G", "G", "S"], fee={"G": 0.0, "S": 0.0})
    assert eq[1] == 1.0
    assert eq[2] == pytest.approx(1.1 * (1 - g.SWITCH_COST) ** 2)
    eq = g.metal_path(gold, silver, ["G", "G", "G"], fee={"G": 0.012, "S": 0.0})
    assert eq[2] == pytest.approx((1 - 0.001) ** 2)


def test_episodes() -> None:
    months = ["m0", "m1", "m2", "m3", "m4"]
    held = ["G", "G", "S", "S", "G"]
    gold, silver = [1.0, 1.0, 1.1, 1.2, 1.2], [1.0, 1.0, 1.3, 1.5, 1.4]
    eps = g.episodes(months, held, gold, silver, [80.0] * 5)
    assert len(eps) == 1
    e = eps[0]
    assert (e["from"], e["to"], e["months"], e["closed"]) == ("m1", "m3", 2, True)
    assert e["silver"] == pytest.approx(0.5) and e["silver_won"]


def _a(sw: float, g0: float, h: float | None = None, won: int = 1, n: int = 2) -> dict[str, Any]:
    def acc(c: float, hh: float) -> dict[str, Any]:
        return {"full": {"cagr": c}, "first": {"cagr": hh}, "second": {"cagr": hh}}

    eps = [{"closed": True, "silver_won": i < won} for i in range(n)]
    return {"SW": acc(sw, sw if h is None else h), "G": acc(g0, g0), "episodes": {"SW": eps}}


def test_judge_a() -> None:
    assert g.judge_a(_a(0.08, 0.07))["status"] == "후속 가치(탐색)"
    assert g.judge_a(_a(0.08, 0.07, h=0.06))["status"] == "불명"
    assert g.judge_a(_a(0.08, 0.07, won=0))["status"] == "불명"
    assert g.judge_a(_a(0.06, 0.07))["status"] == "불리"


def _b(irr: dict[str, float], mdd: dict[str, float]) -> dict[str, Any]:
    return {
        s: {"T": {"irr_liq": 0.15, "mdd": 0.40}, "X": {"irr_liq": irr[s], "mdd": mdd[s]}}
        for s in ("R", "S2")
    }


def test_judge_b() -> None:
    assert g.judge_b(_b({"R": 0.16, "S2": 0.15}, {"R": 0.41, "S2": 0.40}), "X")["status"] == "우위"
    v = g.judge_b(_b({"R": 0.145, "S2": 0.142}, {"R": 0.34, "S2": 0.35}), "X")
    assert v["status"] == "낙폭 교환"
    assert g.judge_b(_b({"R": 0.13, "S2": 0.16}, {"R": 0.30, "S2": 0.3}), "X")["status"] == "불리"
    assert g.judge_b(_b({"R": 0.145, "S2": 0.16}, {"R": 0.38, "S2": 0.4}), "X")["status"] == "불명"


def test_align_forward_fill_and_fee() -> None:
    out = g.align(["a", "b", "c", "d"], {"b": 10.0, "d": 12.0}, fee=0.252)
    assert "a" not in out and out["b"] == 10.0
    assert out["c"] == pytest.approx(10.0 * 0.999) and out["d"] == pytest.approx(12.0 * 0.999**2)


def _data(n: int = 700) -> dict[str, Any]:
    d0 = date(2010, 1, 4)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    lev = {d: 50 * (1 + 0.6 * math.sin(i / 40)) + i * 0.05 for i, d in enumerate(days)}
    div = {d: 30 + i * 0.01 + math.sin(i / 7) for i, d in enumerate(days)}
    vol = {d: 0.15 + 0.15 * abs(math.sin(i / 30)) for i, d in enumerate(days)}
    trend = {d: math.cos(i / 40) > 0 for i, d in enumerate(days)}
    return {"days": days, "lev": lev, "div": div, "vol": vol, "trend": trend,
            "start": days[1], "monthly_krw": 300_000.0}  # fmt: skip


def test_simulate_mix_without_metal_equals_184() -> None:
    data = _data()
    a = simulate(data, data["div"], ref="T", park="cash")
    b = g.simulate_mix(data, data["div"])
    assert a["navs"] == pytest.approx(b["navs"], rel=1e-12)
    assert a["after_tax_liq"] == pytest.approx(b["after_tax_liq"], rel=1e-12)
    assert len(a["sales"]) > 0  # 브레이크가 실제로 돌았다


def test_simulate_mix_holds_metal_and_switches() -> None:
    data = _data()
    days = data["days"]
    metals = {"G": dict.fromkeys(days, 100.0), "S": dict.fromkeys(days, 1.0)}
    gsr = {d: (90.0 if d >= days[300] else 70.0) for d in days}
    tg = g.simulate_mix(data, data["div"], metals)
    assert tg["switches"] == 0 and tg["silver_months"] == 0
    tgs = g.simulate_mix(data, data["div"], metals, gsr)
    assert tgs["switches"] == 1 and tgs["silver_months"] > 5
