from __future__ import annotations

import math
import random

from scripts.grid_room import judge, ohlc_path, p_star, stats, steps_on_lattice

D = math.log1p(0.01)


def _path(levels: list[float]) -> list[float]:
    return [x * D for x in levels]


def test_alternating_path_reverses_every_step() -> None:
    # 0.5 → 1.5 는 격자점 1 을 지나고, 1.5 → 0.5 는 1 을 다시 지날 뿐(중복) — 걸음 없음
    steps, _ = steps_on_lattice(_path([0.5, 1.5, 0.5, 1.5, 0.5, 1.5, 0.5]), D)
    assert list(steps) == []
    # 0 → 2 칸: 점 1·2, 2 → 0 칸: 점 2(중복)·1 — 첫 닿음은 기준점이라 걸음이 아니다
    steps, _ = steps_on_lattice(_path([0.5, 2.5, 0.5, 2.5, 0.5]), D)
    assert list(steps) == [1, -1, 1, -1]


def test_jump_over_several_levels_makes_unit_steps() -> None:
    # 3.7 → 1.2 는 점 3(중복)·2 만 지난다 — 1 에는 닿지 않았다
    steps, where = steps_on_lattice(_path([0.2, 3.7, 1.2]), D)
    assert list(steps) == [1, 1, -1]
    assert list(where) == [1, 1, 2]


def test_negative_log_prices_use_true_floor() -> None:
    steps, _ = steps_on_lattice(_path([-0.5, -2.5, -0.5]), D)
    assert list(steps) == [-1, 1]


def test_random_walk_has_no_room_and_trend_has_negative_edge() -> None:
    # 격자 사이를 촘촘히 지나는 무작위 걸음(실제 1분봉처럼 한 번에 칸의 2% 씩)
    rng = random.Random(7)
    x, walk = 0.5, []
    for _ in range(4_000_000):
        x += rng.gauss(0.0, 0.02)
        walk.append(x)
    steps, _ = steps_on_lattice(_path(walk), D)
    s = stats(steps, 0.01, 10.0)
    assert abs(s["p"] - 0.5) < 3 * s["se_p"]
    assert abs(s["vr10"] - 1) < 3 * s["se_vr10"]
    assert s["edge_per_step_0.0005"] < 0
    steps, _ = steps_on_lattice(_path([i + 0.5 for i in range(3_000)]), D)
    s = stats(steps, 0.01, 10.0)
    assert s["p"] == 0.0 and s["vr10"] == 10.0


def test_mean_reverting_steps_pass_and_breakeven_matches() -> None:
    steps = [1, -1] * 5_000
    s = stats(steps, 0.01, 10.0)
    assert s["p"] == 1.0 and s["vr10"] == 0.0
    assert math.isclose(s["edge_per_step_0.0005"], 0.01 / 2 - 0.0005)
    verdict = judge(s, [s, s], s, 0.01)
    assert verdict["room"]
    assert math.isclose(p_star(0.01, 0.0005), 0.01 / 0.019)
    assert math.isclose(1 - verdict["vr_max"], 2 * 0.0005 / 0.01, rel_tol=1e-3)


def test_ohlc_path_order_follows_candle_colour() -> None:
    path = ohlc_path([10, 10], [12, 12], [9, 9], [11, 9.5])
    assert [round(math.exp(x), 9) for x in path] == [10, 9, 12, 11, 10, 12, 9, 9.5]
