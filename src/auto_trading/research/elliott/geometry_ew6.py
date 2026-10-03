"""EW-6 (문서 104): 변동성 맞춤 꼭짓점 + 최상위 기하만 보는 판독 — 순수 함수.

기존 판독기(EW-3d′~EW-5)와 별개다. 하위 파동은 검증하지 않는다.
꼭짓점은 지금 방향의 극값에서 m × σ 이상 되돌아야 확정된다(인과적, 확정 봉 이후에만 쓸 수 있음).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from auto_trading.core.types import Candle

__all__ = [
    "EW6_LADDER",
    "FIVE_LEG",
    "THREE_LEG",
    "Ew6Event",
    "Pivot6",
    "adaptive_pivots",
    "classify_five",
    "classify_three",
    "ew6_events",
    "primary_label",
]

EW6_LADDER = (2.0, 3.0, 4.5, 7.0, 10.0)
"""문서 104 §2: 꼭짓점 단계 m (σ 의 배수)."""
SIGMA_WINDOW, SIGMA_MIN = 100, 20

IMPULSE = "IMPULSE"
DIAGONAL = "DIAGONAL"
TRIANGLE = "TRIANGLE"
RUNNING_TRIANGLE = "RUNNING_TRIANGLE"
EXPANDING_TRIANGLE = "EXPANDING_TRIANGLE"
ZIGZAG = "ZIGZAG"
FLAT = "FLAT"
FIVE_LEG = (IMPULSE, DIAGONAL, TRIANGLE, RUNNING_TRIANGLE, EXPANDING_TRIANGLE)
THREE_LEG = (FLAT, ZIGZAG)


@dataclass(frozen=True, slots=True)
class Pivot6:
    index: int
    """극값 봉 색인."""
    price: float
    kind: int
    """+1 고점, −1 저점."""
    confirmed: int
    """확정 봉 색인 (그 봉 마감부터 쓸 수 있음)."""


@dataclass(frozen=True, slots=True)
class Ew6Event:
    m: float
    pivot: Pivot6
    five: frozenset[str]
    three: frozenset[str]


def _sigmas(closes: Sequence[float]) -> list[float | None]:
    rets = [0.0] + [math.log(closes[t] / closes[t - 1]) for t in range(1, len(closes))]
    out: list[float | None] = []
    for t in range(len(closes)):
        window = rets[max(1, t - SIGMA_WINDOW + 1) : t + 1]
        if len(window) < SIGMA_MIN:
            out.append(None)
            continue
        mean = sum(window) / len(window)
        out.append(math.sqrt(sum((x - mean) ** 2 for x in window) / (len(window) - 1)))
    return out


def adaptive_pivots(bars: Sequence[Candle], m: float) -> list[Pivot6]:
    """σ 의 m 배 되돌림 지그재그. 같은 봉에서 극값을 갱신하면 그 봉으로는 되돌림을 보지 않는다."""
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    sig = _sigmas([float(b.close) for b in bars])
    out: list[Pivot6] = []
    direction = 0
    hi_i = lo_i = ext_i = -1
    for t in range(len(bars)):
        s = sig[t]
        if s is None:
            continue
        theta = m * s
        if direction == 0:
            if hi_i < 0 or highs[t] >= highs[hi_i]:
                hi_i = t
            if lo_i < 0 or lows[t] <= lows[lo_i]:
                lo_i = t
            if hi_i < t and math.log(highs[hi_i] / lows[t]) >= theta:
                out.append(Pivot6(hi_i, highs[hi_i], +1, t))
                direction, ext_i = -1, t
            elif lo_i < t and math.log(highs[t] / lows[lo_i]) >= theta:
                out.append(Pivot6(lo_i, lows[lo_i], -1, t))
                direction, ext_i = +1, t
            continue
        if direction == +1:
            if highs[t] >= highs[ext_i]:
                ext_i = t
            elif math.log(highs[ext_i] / lows[t]) >= theta:
                out.append(Pivot6(ext_i, highs[ext_i], +1, t))
                direction, ext_i = -1, t
        else:
            if lows[t] <= lows[ext_i]:
                ext_i = t
            elif math.log(highs[t] / lows[ext_i]) >= theta:
                out.append(Pivot6(ext_i, lows[ext_i], -1, t))
                direction, ext_i = +1, t
    return out


def _signed(prices: Sequence[float]) -> list[float]:
    d = 1.0 if prices[1] > prices[0] else -1.0
    return [d * math.log(p) for p in prices]


def classify_five(prices: Sequence[float]) -> frozenset[str]:
    """꼭짓점 6 개(0..5)의 최상위 기하 갈래 (문서 104 §3)."""
    q = _signed(prices)
    q0, q1, q2, q3, q4, q5 = q
    l1, l2, l3, l4, l5 = (abs(b - a) for a, b in pairwise(q))
    out: set[str] = set()
    if q2 > q0 and q3 > q1 and q4 > q1 and q5 > q3 and l3 >= min(l1, l5) and l2 < l1 and l4 < l3:
        out.add(IMPULSE)
    if q2 > q0 and q3 > q1 and q2 < q4 < q1 and q5 > q3 and l1 > l3 > l5 and l2 > l4:
        out.add(DIAGONAL)
    if q0 < q2 < q4 and q1 > q3 > q5:
        out.add(TRIANGLE)
    if q2 < q0 and q3 < q1 and q4 > q2 and q5 < q3 and l3 < l2 and l4 < l3 and l5 < l4:
        out.add(RUNNING_TRIANGLE)
    if q2 < q0 and q3 > q1 and q4 < q2 and q5 > q3:
        out.add(EXPANDING_TRIANGLE)
    return frozenset(out)


def classify_three(prices: Sequence[float]) -> frozenset[str]:
    """꼭짓점 4 개(0..3): 지그재그(이중 포함) · 플랫(이중 3 포함)."""
    q = _signed(prices)
    q0, q1, q2, q3 = q
    a, b, c = (abs(y - x) for x, y in pairwise(q))
    out: set[str] = set()
    if a <= 0:
        return frozenset()
    if q0 < q2 < q1 and b / a < 0.8 and q3 > q1 and c / a <= 2.618:
        out.add(ZIGZAG)
    if 0.8 <= b / a <= 1.382 and 0.618 <= c / a <= 2.0:
        out.add(FLAT)
    return frozenset(out)


def ew6_events(bars: Sequence[Candle], ladder: Sequence[float] = EW6_LADDER) -> list[Ew6Event]:
    """단계마다 꼭짓점이 확정될 때 그 꼭짓점에서 끝나는 3·5 다리 판독. 확정 봉 순."""
    events: list[Ew6Event] = []
    for m in ladder:
        piv = adaptive_pivots(bars, m)
        for k, p in enumerate(piv):
            five = classify_five([x.price for x in piv[k - 5 : k + 1]]) if k >= 5 else frozenset()
            three = classify_three([x.price for x in piv[k - 3 : k + 1]]) if k >= 3 else frozenset()
            events.append(Ew6Event(m, p, five, three))
    events.sort(key=lambda e: (e.pivot.confirmed, e.m))
    return events


def primary_label(events: Sequence[Ew6Event]) -> str:
    """주 해석 (문서 104 §3): 5 다리 갈래가 있으면 그것(여럿이면 TIE), 없으면 플랫 > 지그재그."""
    five = {x for e in events for x in e.five}
    if five:
        return five.pop() if len(five) == 1 else "TIE"
    three = {x for e in events for x in e.three}
    for name in THREE_LEG:
        if name in three:
            return name
    return "NONE"


# ---------------------------------------------------------------- EW-6b: 극단 다리 조합 (문서 104 §6)

LOOKBACK_PIVOTS = 40


def extremal_vertex_sets(
    pivots: Sequence[Pivot6], k: int, legs: int, lookback: int = LOOKBACK_PIVOTS
) -> list[tuple[int, ...]]:
    """k 에서 끝나는 ``legs`` 다리 꼭짓점 조합 전부. 각 다리는 시작·끝이 그 구간의 극값
    (끝이 고점이면 구간 안 모든 꼭짓점 가격 ≤ 끝, 시작 ≥ ... 저점 반대)."""
    lo = max(0, k - lookback)
    out: list[tuple[int, ...]] = []

    def back(end: int, left: int, acc: tuple[int, ...]) -> None:
        if left == 0:
            out.append(acc)
            return
        top = pivots[end]
        best_other = None  # 구간 안 반대쪽 극값 (시작 후보가 이걸 이겨야 함)
        for j in range(end - 1, lo - 1, -1):
            p = pivots[j]
            if p.kind == top.kind:
                # 같은 쪽 꼭짓점이 끝을 넘으면 더 앞의 시작은 불가
                if (top.kind > 0 and p.price > top.price) or (top.kind < 0 and p.price < top.price):
                    return
                continue
            better = best_other is None or (
                p.price <= best_other if top.kind > 0 else p.price >= best_other
            )
            if better:
                best_other = p.price
                back(j, left - 1, (j, *acc))

    back(k, legs, (k,))
    return out


def ew6b_labels(pivots: Sequence[Pivot6], k: int) -> tuple[frozenset[str], frozenset[str]]:
    """k 에서 끝나는 극단 다리 조합 중 어느 하나라도 맞는 (5 다리 갈래, 3 다리 갈래)."""
    five: set[str] = set()
    three: set[str] = set()
    for vs in extremal_vertex_sets(pivots, k, 5):
        five |= classify_five([pivots[i].price for i in vs])
    for vs in extremal_vertex_sets(pivots, k, 3):
        three |= classify_three([pivots[i].price for i in vs])
    return frozenset(five), frozenset(three)


def ew6b_events(bars: Sequence[Candle], m: float = 2.0) -> list[Ew6Event]:
    """가장 촘촘한 단계 m 의 꼭짓점 위에서 극단 다리 조합으로 판독 (확정 봉 순)."""
    piv = adaptive_pivots(bars, m)
    events = []
    for k in range(len(piv)):
        five, three = ew6b_labels(piv, k)
        events.append(Ew6Event(m, piv[k], five, three))
    return events
