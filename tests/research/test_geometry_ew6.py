"""EW-6 (문서 104): 변동성 맞춤 꼭짓점과 최상위 기하 규칙."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

import pytest

from auto_trading.core.types import Candle
from auto_trading.research.elliott.geometry_ew6 import (
    adaptive_pivots,
    classify_five,
    classify_three,
    ew6_events,
    primary_label,
)


@pytest.mark.parametrize(
    ("prices", "label"),
    [
        ([100, 110, 104, 125, 117, 130], "IMPULSE"),
        ([100, 110, 103, 111.5, 106, 113], "DIAGONAL"),  # 4 파(106)가 1 파 끝(110) 아래 · 수렴
        ([100, 120, 104, 116, 108, 113], "TRIANGLE"),
        ([100, 110, 97, 106, 100, 104], "RUNNING_TRIANGLE"),
        ([100, 105, 98, 108, 95, 112], "EXPANDING_TRIANGLE"),
    ],
)
def test_five_leg_shapes(prices: list[float], label: str) -> None:
    assert label in classify_five(prices)


def test_five_leg_shapes_are_exclusive_on_the_examples() -> None:
    assert classify_five([100, 110, 104, 125, 117, 130]) == {"IMPULSE"}
    assert "IMPULSE" not in classify_five([100, 110, 103, 111.5, 106, 113])  # 겹침


def test_down_direction_is_mirrored() -> None:
    up = [100, 110, 104, 125, 117, 130]
    down = [10_000 / p for p in up]
    assert classify_five(down) == classify_five(up)


def test_three_leg_zigzag_and_flat() -> None:
    assert classify_three([100, 110, 105, 116]) == {"ZIGZAG"}
    assert classify_three([100, 110, 100.5, 111]) == {"FLAT"}
    assert classify_three([100, 110, 112, 120]) == frozenset()  # B 가 A 와 같은 방향 (꼭짓점 아님)


def _bars(closes: list[float]) -> list[Candle]:
    return [
        Candle(
            open_ms=k * 3_600_000,
            close_ms=(k + 1) * 3_600_000 - 1,
            open=Decimal(str(c)),
            high=Decimal(str(c)),
            low=Decimal(str(c)),
            close=Decimal(str(c)),
            volume=Decimal(1),
        )
        for k, c in enumerate(closes)
    ]


def _wiggle_then_swings() -> list[float]:
    base = [100 + (0.2 if k % 2 else -0.2) for k in range(30)]  # σ 약 0.4%
    up = [100 + k for k in range(1, 11)]  # +10%
    down = [110 - k for k in range(1, 6)]  # −5%
    up2 = [105 + 1.5 * k for k in range(1, 9)]
    return base + up + down + up2


def test_adaptive_pivots_are_causal_and_confirmed_after_reversal() -> None:
    closes = _wiggle_then_swings()
    piv = adaptive_pivots(_bars(closes), 3.0)
    high = next(p for p in piv if p.kind == +1 and p.price == 110)
    assert high.index == 39
    assert high.confirmed > high.index
    # 앞부분만 줘도 같은 꼭짓점이 같은 봉에서 확정된다 (미래를 보지 않음)
    again = adaptive_pivots(_bars(closes[: high.confirmed + 1]), 3.0)
    assert any(p.index == 39 and p.confirmed == high.confirmed for p in again)


def test_events_and_primary_label() -> None:
    events = ew6_events(_bars(_wiggle_then_swings()), (3.0,))
    assert all(a.pivot.confirmed <= b.pivot.confirmed for a, b in pairwise(events))
    assert primary_label([]) == "NONE"
