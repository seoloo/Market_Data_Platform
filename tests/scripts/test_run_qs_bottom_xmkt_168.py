from __future__ import annotations

import math

import pytest
from scripts.run_qs_bottom_xmkt_168 import (
    FEE,
    OVERSOLD_C,
    TURNS_C,
    close_marks,
    cluster,
    episode_edge,
    lev3,
    off_episodes,
)


def test_close_marks_shape_and_no_future() -> None:
    close = [100 * (1 + 0.25 * math.sin(i / 30)) for i in range(420)]
    m = close_marks(close)
    assert m[:252] == [None] * 252
    last = m[-1]
    assert (
        last is not None
        and len(last[0]) == len(OVERSOLD_C) == 10
        and len(last[1]) == len(TURNS_C) == 8
    )
    changed = close[:380] + [c * 0.4 for c in close[380:]]
    assert close_marks(changed)[:380] == m[:380]


def test_off_episodes_ignores_unfinished() -> None:
    on = [None, True, False, False, True, False]
    assert off_episodes(on) == [(2, 4)]


def test_episode_edge_hold_and_stop() -> None:
    close = [100.0, 90.0, 80.0, 85.0, 95.0, 100.0]
    lev = lev3(close)
    bottom = [False, True, False, False, False, False]
    e, log = episode_edge(close, lev, bottom, 0, 5)
    # 1 일 신호 → 2 일 종가(80)에 1/3 사서 5 일(재진입)까지
    assert log == [{"buy_i": 2, "stopped": False, "ret": round(lev[5] / lev[2] - 1, 4)}]
    assert e == pytest.approx((lev[5] / lev[2] - 1) / 3)
    # 산 뒤 지수가 15% 더 내리면 손절
    close2 = [100.0, 90.0, 80.0, 66.0, 70.0, 75.0]
    lev2 = lev3(close2)
    e2, log2 = episode_edge(close2, lev2, bottom, 0, 5)
    assert log2[0]["stopped"]
    assert e2 == pytest.approx((lev2[3] / lev2[2] * (1 - FEE) ** 2 - 1) / 3)


def test_cluster_merges_overlapping_periods() -> None:
    iv = [
        ("A", "2020-02-20", "2020-04-10"),
        ("B", "2020-03-01", "2020-05-01"),
        ("C", "2021-01-01", "2021-02-01"),
    ]
    assert cluster(iv) == [[0, 1], [2]]
