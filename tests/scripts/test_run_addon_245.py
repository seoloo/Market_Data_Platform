"""245 — 추가 매수 신호(눌림 뒤 회복 · +10%) · 한 번만 추가 · 판정 (가짜 판)."""

from __future__ import annotations

import pytest
from scripts.run_exit_242 import Market

from scripts import run_addon_245 as m


def test_pull_needs_dip_below_ma_then_recover() -> None:
    close: list[float | None] = [100.0] * 25 + [90.0, 95.0, 101.0]
    p = m.Pos(1.0, 100.0, 100.0, 20, 100.0)
    arm = m.Arm("PULL", 0.05, "pull")
    assert not m.add_signal(arm, p, close, 24)  # 아직 안 눌림 (20 일선 = 100, 종가 100)
    assert not m.add_signal(arm, p, close, 25) and p.dipped  # 20 일선 아래
    assert m.add_signal(arm, p, close, 27)  # 다시 위
    p.added = True
    assert not m.add_signal(arm, p, close, 27)  # 한 번만


def test_up10_and_no_add_arm() -> None:
    close: list[float | None] = [100.0] * 21 + [110.0, 111.0]
    p = m.Pos(1.0, 100.0, 100.0, 20, 100.0)
    assert not m.add_signal(m.Arm("UP10", 0.05, "up"), p, close, 21)  # 정확히 +10% 는 아님
    assert m.add_signal(m.Arm("UP10", 0.05, "up"), p, close, 22)
    assert not m.add_signal(m.Arm("FULL", 0.10, None), p, close, 22)


def test_simulate_adds_once_next_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "trend_up", lambda xs, t: True)
    monkeypatch.setattr(m, "trend_off", lambda xs, t: False)
    n = 40
    close: list[float | None] = [100.0] * 21 + [115.0] * 2 + [130.0] * (n - 23)
    mk = Market(
        [f"2009-{1 + k // 20:02d}-{1 + k % 20:02d}" for k in range(n)],
        {"AAA": close},
        None,
        None,
        [False] * 19 + [True] + [False] * (n - 20),  # 첫 월말에만 산다
        lambda t: ["AAA"],
    )
    up = m.simulate_add(mk, m.Arm("UP10", 0.05, "up"), "2009-01-01")
    full = m.simulate_add(mk, m.Arm("FULL", 0.10, None), "2009-01-01")
    half = m.simulate_add(mk, m.Arm("HALF", 0.05, None), "2009-01-01")
    assert up.adds == 1 and full.adds == 0
    assert half.equity[-1] < up.equity[-1] < full.equity[-1]  # 추가분은 115 에 사 130 까지만 벎


def test_judge() -> None:
    def row(sh: float, mdd: float, h1: float, h2: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": sh, "mdd": mdd},
            "first": {"sharpe": h1},
            "second": {"sharpe": h2},
        }

    full = row(0.30, 0.50, 0.2, 0.4)
    kr = {"FULL": full, "PULL": row(0.45, 0.54, 0.3, 0.5), "UP10": row(0.50, 0.60, 0.3, 0.5)}
    assert m.judge(kr) == "ADOPT_PULL"  # UP10 은 낙폭 +10%p
    kr["PULL"] = row(0.45, 0.54, 0.1, 0.5)
    assert m.judge(kr) == "NONE"
