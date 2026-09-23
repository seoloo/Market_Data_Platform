"""07 후보 상태의 정의·잠금·인과성 검사 (수익은 다루지 않는다)."""

from __future__ import annotations

import pytest
from scripts.measure_w_candidates import (
    Hours,
    abs_move_rank,
    position_7d,
    state_wa,
    state_wb,
    triggers,
)

WEEK = 7 * 24


def _hours(closes: list[float]) -> Hours:
    return Hours(
        open_ms=[i * 3_600_000 for i in range(len(closes))],
        high=[c * 1.001 for c in closes],
        low=[c * 0.999 for c in closes],
        close=closes,
    )


def test_wa_requires_24h_decline_and_bottom_of_week() -> None:
    closes = [100.0] * WEEK + [90.0]
    hours = _hours(closes)
    last = len(closes) - 1
    assert position_7d(hours, last) == pytest.approx(0.0, abs=0.01)
    assert state_wa(hours, last, [])
    rising = _hours([90.0] * WEEK + [100.0])
    assert not state_wa(rising, last, [])
    assert not state_wa(hours, WEEK - 2, [])  # 7일 이력이 없으면 판단하지 않는다


def test_wa_ignores_future_bars() -> None:
    closes = [100.0] * WEEK + [90.0]
    before = state_wa(_hours(closes), WEEK, [])
    after = state_wa(_hours([*closes, 200.0, 50.0]), WEEK, [])
    assert before == after


def test_wb_uses_causal_rank_of_absolute_move() -> None:
    closes = [100.0 + (i % 2) * 0.1 for i in range(800)] + [130.0]
    ranks = abs_move_rank(closes)
    assert ranks[:24] == [None] * 24
    assert ranks[-1] == pytest.approx(1.0, abs=0.01)
    assert state_wb(_hours(closes), len(closes) - 1, ranks)
    extended = abs_move_rank([*closes, 10.0, 500.0])
    assert extended[len(closes) - 1] == ranks[-1]


def test_lock_blocks_retriggers() -> None:
    hours = _hours([1.0] * 30)
    always = triggers(hours, lambda _h, _i, _r: True, [], lock=12)
    assert always == [0, 12, 24]
