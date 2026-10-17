"""259 — 본전 이동 · 계단식 손절 · +20% 뒤 50 일선, 대조(258 ARM)와 같음 · 판정 (가짜 자료)."""

from __future__ import annotations

import pytest
from scripts.run_exit_242 import Market
from tests.scripts.test_run_takeprofit_258 import _days

from scripts import run_protect_259 as m
from scripts import run_takeprofit_258 as tp


def _mk(xs: list[float | None]) -> Market:
    n = len(xs)
    return Market(_days(n), {"X": xs}, None, None, [True] * n, lambda _t: ["X"])


def _up_then_back(gain: float, days_up: int = 20, days_down: int = 30) -> list[float | None]:
    """130 일 오름(추세) → t=130 매수 → +gain 까지 오른 뒤 진입가 아래로 되돌아옴."""
    xs: list[float | None] = [100.0 * 1.003**t for t in range(131)]
    entry = xs[130]
    assert entry is not None
    xs += [entry * (1 + gain * k / days_up) for k in range(1, days_up + 1)]
    xs += [
        entry * (1 + gain) * (1 - (gain + 0.1) / (1 + gain) * k / days_down)
        for k in range(1, days_down + 1)
    ]
    return xs


def test_step_floor() -> None:
    assert m.step_floor(100.0, 119.0) is None
    assert m.step_floor(100.0, 120.0) == pytest.approx(100.0)
    assert m.step_floor(100.0, 145.0) == pytest.approx(120.0)
    assert m.step_floor(100.0, 165.0) == pytest.approx(140.0)


def test_arm_matches_258() -> None:
    mk = _mk(_up_then_back(0.3))
    a = m.simulate(mk, "ARM", start_date=mk.days[0])
    b = tp.simulate(mk, "ARM", start_date=mk.days[0])
    assert a.equity == pytest.approx(b.equity)


def test_be10_exits_at_breakeven() -> None:
    mk = _mk(_up_then_back(0.15))
    r = m.simulate(mk, "BE10", start_date=mk.days[0])
    x = r.trades[0]
    assert x["reason"] in ("protect", "slow")
    if x["reason"] == "protect":
        assert x["exit_px"] / x["entry_px"] == pytest.approx(1.0, abs=0.02)


def test_step20_keeps_twenty_after_forty() -> None:
    mk = _mk(_up_then_back(0.45))
    r = m.simulate(mk, "STEP20", start_date=mk.days[0])
    x = r.trades[0]
    assert x["exit_px"] / x["entry_px"] >= 1.15  # +40% 에 닿은 뒤 손절가는 +20%


def test_ma50_only_after_gain() -> None:
    mk = _mk(_up_then_back(0.10))  # +20% 에 안 닿음 → 50 일선 매도 없음
    r = m.simulate(mk, "MA50", start_date=mk.days[0])
    b = m.simulate(mk, "ARM", start_date=mk.days[0])
    assert r.equity == pytest.approx(b.equity)


def test_judge_and_gave_back() -> None:
    def row(s: float, s1: float, s2: float, mdd: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": s, "mdd": mdd},
            "first": {"sharpe": s1},
            "second": {"sharpe": s2},
        }

    base = row(0.75, 0.43, 0.95, 0.28)
    us = {"ARM": base, "BE10": row(0.9, 0.5, 1.0, 0.3), "STEP20": row(0.7, 0.5, 1, 0.2)}
    us["MA50"] = row(0.8, 0.5, 1.0, 0.2)
    kr = {k: row(0.3, 0, 0, 0.5) for k in m.ARMS}
    assert m.judge(us, kr)["verdict"] == "ADOPT_BE10"
    kr["BE10"] = row(0.2, 0, 0, 0.5)
    assert m.judge(us, kr)["verdict"] == "US_ONLY"
    mk = _mk(_up_then_back(0.3))
    g = m.gave_back(mk, m.simulate(mk, "ARM", start_date=mk.days[0]))
    assert g["up20_trades"] >= 1
