from __future__ import annotations

import math
import random

from scripts.run_order_kr_161 import (
    design,
    nw_mean_se,
    predict,
    rank_center,
    ridge,
    sorted_order,
    xtx,
)


def test_rank_center_handles_ties_and_range() -> None:
    assert rank_center([3.0, 1.0, 2.0]) == [0.5, -0.5, 0.0]
    tied = rank_center([1.0, 1.0, 2.0, 3.0])
    assert tied[0] == tied[1] == (0 + 1) / 2 / 3 - 0.5
    assert tied[3] == 0.5
    r = rank_center([5.0, 5.0, 1.0])
    assert r[0] == r[1] and r[2] == -0.5 and abs(sum(r)) < 1e-12


def test_sorted_design_ignores_time_order_but_ordered_design_does_not() -> None:
    base = [0.1, -0.2, 0.3]
    segs = [[0.4, 0.1, -0.3], [-0.2, 0.2, 0.25], [0.05, -0.1, 0.1]]
    raw = [0.03, -0.01, 0.0]
    perm = [2, 0, 1]
    segs_p = [segs[k] for k in perm]
    raw_p = [raw[k] for k in perm]
    s1 = design(base, segs, sorted_order(raw))
    s2 = design(base, segs_p, sorted_order(raw_p))
    assert s1 == s2  # S: 같은 구간 집합이면 시간 순서가 바뀌어도 같다
    o1 = design(base, segs, (0, 1, 2))
    o2 = design(base, segs_p, (0, 1, 2))
    assert o1 != o2  # O: 순서가 바뀌면 달라진다
    assert len(o1) == 14
    assert o1[12] == segs[0][0] * segs[1][2] and o1[13] == segs[1][0] * segs[2][2]


def test_ridge_recovers_linear_coefficients() -> None:
    rng = random.Random(1)
    xs = [[rng.uniform(-0.5, 0.5) for _ in range(3)] for _ in range(2000)]
    ys = [0.2 + 0.5 * a - 0.3 * b + 0.1 * c for a, b, c in xs]
    a, b = xtx(xs, ys)
    w = ridge(a, b, alpha=1e-9)
    assert all(abs(u - v) < 1e-6 for u, v in zip(w, [0.2, 0.5, -0.3, 0.1], strict=True))
    assert abs(predict(w, [0.1, 0.1, 0.1]) - (0.2 + 0.03)) < 1e-6


def test_nw_se_reduces_to_iid_at_lag_zero() -> None:
    xs = [0.1, -0.2, 0.05, 0.3, -0.1]
    m, se = nw_mean_se(xs, lag=0)
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    assert math.isclose(se, math.sqrt(var / len(xs)))
