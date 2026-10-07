from __future__ import annotations

import math
import random
from itertools import pairwise

from auto_trading.research.patterns_lmw import (
    BANDWIDTH,
    detect,
    extrema,
    find_dbot,
    find_ihs,
    smooth,
)


def _path(knots: list[tuple[int, float]]) -> list[float]:
    xs: list[float] = []
    for (i0, v0), (i1, v1) in pairwise(knots):
        xs.extend(v0 + (v1 - v0) * (i - i0) / (i1 - i0) for i in range(i0, i1))
    xs.append(knots[-1][1])
    return xs


# 38 칸 창, 마지막 극점이 칸 34(= 37 − 3)에서 끝나는 교과서 모양
IHS = _path([(0, 125), (5, 100), (12, 112), (19, 88), (26, 112), (34, 100.5), (37, 112)])
DBOT = _path([(0, 125), (6, 100), (20, 115), (34, 100.8), (37, 110)])


def test_detects_textbook_shapes_completed_exactly_lag_before_end() -> None:
    assert [(p.kind, p.points) for p in detect(IHS)] == [("IHS", (5, 12, 19, 26, 34))]
    assert [(p.kind, p.points) for p in detect(DBOT)] == [("DBOT", (6, 34))]


def test_pattern_completed_earlier_or_later_is_not_detected_in_this_window() -> None:
    assert detect([*IHS[1:], 112.0]) == []  # 하루 늦은 창: 완성이 4 칸 앞
    assert [p for p in detect(IHS, lag=2) if p.kind == "IHS"] == []  # 완성이 창 끝 3 칸 앞 ≠ 2


def test_shoulders_outside_tolerance_are_rejected() -> None:
    lopsided = _path([(0, 125), (5, 100), (12, 112), (19, 88), (26, 112), (34, 104), (37, 112)])
    assert not [p for p in detect(lopsided) if p.kind == "IHS"]


def test_double_bottom_needs_gap_over_22() -> None:
    near = _path([(0, 125), (14, 100), (24, 115), (34, 100.8), (37, 110)])
    assert not [p for p in detect(near) if p.kind == "DBOT"]


def test_bandwidth_is_fixed_and_smooth_preserves_constants() -> None:
    assert BANDWIDTH == 1.25
    assert all(abs(x - 7.0) < 1e-12 for x in smooth([7.0] * 38, BANDWIDTH))


def test_scale_invariant_and_deterministic() -> None:
    random.seed(7)
    w = [100 * math.exp(sum(random.gauss(0, 0.02) for _ in range(k))) for k in range(38)]
    assert detect(w) == detect([x * 37.0 for x in w])
    assert detect(w) == detect(list(w))


def test_extrema_alternate() -> None:
    random.seed(3)
    w = [100 + random.gauss(0, 3) for _ in range(38)]
    ext = extrema(w, smooth(w, 1.0))
    kinds = [k for _, _, k in ext]
    assert all(a != b for a, b in pairwise(kinds))
    assert find_ihs(ext, -1) == [] and find_dbot(ext, -1) == []
