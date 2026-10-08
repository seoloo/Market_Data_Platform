from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from scripts.run_qs_switch import leveraged, simulate
from scripts.run_qs_trim_169 import drawdowns, simulate_trim

DAYS = [(date(2001, 1, 1) + timedelta(days=k)).isoformat() for k in range(800)]


def _px() -> dict[str, float]:
    return {d: 100 * (1 + 0.35 * math.sin(i / 50)) * (1.0004**i) for i, d in enumerate(DAYS)}


def _sig(px: dict[str, float]) -> dict[str, bool | None]:
    return {d: (None if i < 30 else px[d] > px[DAYS[i - 30]]) for i, d in enumerate(DAYS)}


def test_drawdowns_from_trailing_year_high() -> None:
    px = {d: (100.0 if i < 10 else 80.0) for i, d in enumerate(DAYS[:20])}
    dd = drawdowns(DAYS[:20], px)
    assert dd[DAYS[5]] == 0.0
    assert dd[DAYS[15]] == pytest.approx(0.20)


def test_no_thresholds_equals_qs() -> None:
    px = _px()
    lev = leveraged(DAYS, px, {}, 3.0, 0.0084)
    sig = _sig(px)
    dd = drawdowns(DAYS, px)
    base = simulate(DAYS, px, lev, sig, {}, mode="QS", start=DAYS[0], end=DAYS[-1])
    r, st = simulate_trim(
        DAYS, px, lev, sig, {}, dd, start=DAYS[0], end=DAYS[-1], thresholds=(), frac=0.1
    )
    assert r.final == pytest.approx(base.final)
    assert r.tax_paid == pytest.approx(base.tax_paid)
    assert st == {"sells": 0, "seed_to_tqqq": 0, "seed_back": 0}


def test_thresholds_sell_once_per_episode() -> None:
    px = _px()
    lev = leveraged(DAYS, px, {}, 3.0, 0.0084)
    sig = _sig(px)
    dd = drawdowns(DAYS, px)
    _r, st = simulate_trim(
        DAYS, px, lev, sig, {}, dd, start=DAYS[0], end=DAYS[-1], thresholds=(0.10, 0.20), frac=0.1
    )
    assert st["sells"] > 0
    assert st["sells"] <= 2 * (st["seed_to_tqqq"] + st["seed_back"] + 1)
