"""260 — 비용선 잠금 · 그리드 손절 올리기 · 갭 체결 기록 · 판정 (가짜 자료)."""

from __future__ import annotations

import pytest
from scripts.run_exit_242 import Market
from tests.scripts.test_run_protect_259 import _up_then_back
from tests.scripts.test_run_takeprofit_258 import _days

from scripts import run_grid_stop_260 as m
from scripts import run_takeprofit_258 as tp


def _mk(xs: list[float | None], lows: list[float | None] | None = None) -> Market:
    n = len(xs)
    low = {"X": lows} if lows is not None else None
    return Market(_days(n), {"X": xs}, low, None, [True] * n, lambda _t: ["X"])


def test_grid_floor_and_lock() -> None:
    c = 1 + m.COST_LINE
    assert m.grid_floor(100.0, 104.9, 0.05) is None
    assert m.grid_floor(100.0, 105.0, 0.05) == pytest.approx(100 * c)
    assert m.grid_floor(100.0, 112.0, 0.05) == pytest.approx(105.0)
    assert m.grid_floor(100.0, 131.0, 0.10) == pytest.approx(120.0)
    lock = m.floor_for("LOCK5")
    assert lock(100.0, 104.0) is None and lock(100.0, 150.0) == pytest.approx(100 * c)
    assert m.floor_for("ARM")(100.0, 200.0) is None


def test_arm_matches_258() -> None:
    mk = _mk(_up_then_back(0.3))
    a = m.simulate(mk, "ARM", start_date=mk.days[0])
    b = tp.simulate(mk, "ARM", start_date=mk.days[0])
    assert a.equity == pytest.approx(b.equity)


def test_grid5_sells_above_cost_and_records() -> None:
    mk = _mk(_up_then_back(0.12))  # +12% 까지 → 손절가 +5%
    r = m.simulate(mk, "GRID5", start_date=mk.days[0])
    x = r.trades[0]
    assert x["reason"] in ("grid", "slow")
    if x["reason"] == "grid":
        assert x["exit_px"] / x["entry_px"] == pytest.approx(1.05, abs=0.02) and x["ret"] > 0
    s = m.protect_stats(r)
    assert s["up5_trades"] == 1


def test_gap_below_line_is_counted() -> None:
    xs = _up_then_back(0.12)
    lows = list(xs)
    mk = _mk(xs, lows)
    r0 = m.simulate(mk, "GRID5", start_date=mk.days[0])
    i = next((x["exit_i"] for x in r0.trades if x["reason"] == "grid"), None)
    if i is None:
        pytest.skip("느린 나가기가 먼저")
    # 그 날 갭 하락: 저가를 선보다 10% 아래로 (시가 없음 → 선 가격에 체결로 셈)
    v = xs[i]
    assert v is not None
    lows[i] = v * 0.8
    r = m.simulate(_mk(xs, lows), "GRID5", start_date=mk.days[0])
    assert m.protect_stats(r)["grid_exits"] >= 1


def test_judge() -> None:
    def row(s: float, s1: float, s2: float, mdd: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": s, "mdd": mdd},
            "first": {"sharpe": s1},
            "second": {"sharpe": s2},
        }

    base = row(0.6, 0.4, 0.8, 0.3)
    main = {"ARM": base, "LOCK5": row(0.75, 0.5, 0.9, 0.3), "GRID5": row(0.5, 0.5, 0.9, 0.3)}
    main["GRID10"] = row(0.6, 0.5, 0.9, 0.3)
    kr = {k: row(0.3, 0, 0, 0.5) for k in m.ARMS}
    assert m.judge(main, kr)["verdict"] == "ADOPT_LOCK5"
    kr["LOCK5"] = row(0.2, 0, 0, 0.5)
    assert m.judge(main, kr)["verdict"] == "SP400_ONLY"
    main["LOCK5"] = row(0.65, 0.5, 0.9, 0.3)
    assert m.judge(main, kr)["verdict"] == "KEEP_ARM"
