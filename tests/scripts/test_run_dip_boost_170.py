from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from scripts.run_dip_boost_170 import simulate_boost

DAYS = [(date(2005, 1, 3) + timedelta(days=k)).isoformat() for k in range(500)]


def _px() -> dict[str, float]:
    return {d: 100 * (1 + 0.2 * math.sin(i / 30)) * (1.0003**i) for i, d in enumerate(DAYS)}


def test_seed_in_index_without_signal_equals_all_in_index() -> None:
    px = _px()
    a = simulate_boost(
        DAYS, px, px, {}, {}, start=DAYS[0], end=DAYS[-1], reserve="현금", theta=0, base_share=1.0
    )
    b = simulate_boost(DAYS, px, px, {}, {}, start=DAYS[0], end=DAYS[-1], reserve="지수", theta=0)
    assert a[0] == pytest.approx(b[0])
    assert a[2] == b[2] == 0


def test_cash_seed_deploys_half_on_signal_with_gap() -> None:
    px = _px()
    rsi = dict.fromkeys(DAYS, 20.0)  # 늘 신호 → 10 거래일마다
    v, _dd, buys = simulate_boost(
        DAYS, px, px, {}, rsi, start=DAYS[0], end=DAYS[-1], reserve="현금", theta=30
    )
    assert buys == pytest.approx(len(DAYS) / 10, abs=2)
    v0, _, _ = simulate_boost(
        DAYS, px, px, {}, {}, start=DAYS[0], end=DAYS[-1], reserve="현금", theta=30
    )
    assert v != v0
