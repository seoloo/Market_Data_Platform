"""V1 limit_fill 이 진입을 못 하는 이유 세기 (보고만, 실행기 교체 없음)."""

from __future__ import annotations

from collections import Counter
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

D = Decimal


def reason(previous: Any, bar: Any, side: int, symbol: str) -> str:
    """V1 execution.limit_fill(cost=1) 과 같은 순서로 판단하고 실패 이유를 돌려준다."""
    if previous[5] <= 0 or bar[5] <= 0:
        return "VOLUME_ZERO"
    tick = D("0.1") if symbol == "BTCUSDT" else D("0.01")
    cap = D(str(previous[4])) * (1 + D(side) * D("0.003"))
    px = D(str(bar[1])) * (1 + D(side) * D("0.00045"))
    px = (px / tick).to_integral_value(rounding=ROUND_CEILING if side == 1 else ROUND_FLOOR) * tick
    if (side == 1 and px > cap) or (side == -1 and px < cap):
        return "CAP"
    if not D(str(bar[3])) <= px <= D(str(bar[2])):
        # 사기 가격이 그 분 최고가보다 높음(팔기는 최저가보다 낮음) = 시가보다 불리한 쪽,
        # 즉 주문 시점에 이미 바로 체결될 자리였다.
        marketable = (side == 1 and px >= D(str(bar[1]))) or (side == -1 and px <= D(str(bar[1])))
        return "OUT_OF_RANGE_MARKETABLE" if marketable else "OUT_OF_RANGE_OTHER"
    return "FILLABLE"


def count(
    times: Any, symbols: Any, assets: dict[str, Any], names: tuple[str, ...]
) -> dict[str, Any]:
    out: dict[str, Counter[str]] = {"LONG": Counter(), "SHORT": Counter()}
    for t, s in zip(times, symbols, strict=True):
        sym = names[int(s)]
        a = assets[sym]["minutes"]
        ix = (int(t) + 60000 - int(a[0, 0])) // 60000
        if ix <= 0 or ix >= len(a) or int(a[ix, 0]) != int(t) + 60000:
            out["LONG"]["DATA_INCOMPLETE"] += 1
            out["SHORT"]["DATA_INCOMPLETE"] += 1
            continue
        for side, name in ((1, "LONG"), (-1, "SHORT")):
            out[name][reason(a[ix - 1], a[ix], side, sym)] += 1
    return {k: dict(v) for k, v in out.items()}
