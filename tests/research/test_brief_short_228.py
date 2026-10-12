"""문서 228 — 숏 조건 · 1 일 보유 · +5% 손절 · 0.5 배 · 계좌 합치기 · AG 정리선."""

from __future__ import annotations

from auto_trading.research.brief_short_228 import (
    combine,
    random_short_totals,
    short_ok,
    simulate_shorts,
)
from auto_trading.research.laya_short_227 import DAY_MS, FEE, Bar, features


def test_short_ok_needs_full_downtrend_and_filters() -> None:
    down = features([400.0 - i for i in range(250)], [0.0001] * 21)
    assert down is not None and short_ok(down)
    up = features([100.0 + i for i in range(250)], [0.0001] * 21)
    assert up is not None and not short_ok(up)
    neg_fund = features([400.0 - i for i in range(250)], [-0.0001] * 21)
    assert neg_fund is not None and not short_ok(neg_fund)


def _flat(n: int) -> dict[int, Bar]:
    return {i * DAY_MS: Bar(100, 100, 100, 100) for i in range(n)}


def test_one_day_short_half_size_and_stop() -> None:
    perp = {"X": _flat(20)}
    [t] = simulate_shorts({"X": [0]}, 1, perp, {"X": []})
    assert t.entry_day == DAY_MS and t.exit_day == 2 * DAY_MS
    assert abs(t.ret - 0.5 * (-2 * FEE)) < 1e-12
    perp["X"][DAY_MS] = Bar(100, 106, 100, 104)
    [s] = simulate_shorts({"X": [0]}, 3, perp, {"X": []})
    assert s.stopped and s.exit_day == 2 * DAY_MS
    assert abs(s.ret - 0.5 * ((100 - 105 * 1.01) / 100 - FEE - FEE * 105 * 1.01 / 100)) < 1e-12


def test_combine_adds_shorts_on_exit_day_and_halts() -> None:
    perp = {"X": _flat(20)}
    [t] = simulate_shorts({"X": [0]}, 1, perp, {"X": []})
    rets, halted = combine([DAY_MS, 2 * DAY_MS], [0.01, 0.0], [t])
    assert rets == [0.01, t.ret]
    assert halted is None
    rets2, h2 = combine([0, DAY_MS, 2 * DAY_MS], [-0.5, -0.5, 0.2], [])
    assert h2 == DAY_MS and rets2[2] == 0.0


def test_random_totals_shape() -> None:
    perp = {"X": _flat(60), "Y": _flat(60)}
    days = {"X": [i * DAY_MS for i in range(40)], "Y": [i * DAY_MS for i in range(40)]}
    got = random_short_totals(days, 4, 1, perp, {"X": [], "Y": []}, reps=3)
    assert len(got) == 3
    assert all(abs(x - ((1 - FEE) ** 4 - 1)) < 1e-9 for x in got)
