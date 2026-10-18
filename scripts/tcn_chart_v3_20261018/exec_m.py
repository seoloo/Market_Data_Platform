"""실행기 E2: '바로 체결될 지정가'는 불리 가격 전체로 체결.

V1 실행기는 고치지 않고 ``e2()`` 안에서만 잠시 바꿔 끼운다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from scripts.tcn_chart_20261018 import execution

D = Decimal


def marketable_fill(previous: Any, bar: Any, side: int, symbol: str, cost: int) -> Decimal | None:
    """V1 limit_fill 과 같되, 분 고저 범위 밖이어도 시가보다 불리한 쪽이면 그 가격으로 체결."""
    if previous[5] <= 0 or bar[5] <= 0:
        return None
    tick = D("0.1") if symbol == "BTCUSDT" else D("0.01")
    cap = D(str(previous[4])) * (1 + D(side) * D("0.003"))
    opening = D(str(bar[1]))
    px = opening * (1 + D(side) * D("0.00045") * cost)
    px = (px / tick).to_integral_value(rounding=ROUND_CEILING if side == 1 else ROUND_FLOOR) * tick
    if (side == 1 and px > cap) or (side == -1 and px < cap):
        return None
    if D(str(bar[3])) <= px <= D(str(bar[2])):
        return px
    if (side == 1 and px >= opening) or (side == -1 and px <= opening):
        return px
    return None


@contextmanager
def e2() -> Iterator[None]:
    original = execution.limit_fill
    execution.limit_fill = marketable_fill
    try:
        yield
    finally:
        execution.limit_fill = original
