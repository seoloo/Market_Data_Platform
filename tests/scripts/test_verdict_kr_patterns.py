from __future__ import annotations

import math
import statistics

from scripts.verdict_kr_patterns import (
    HOLD,
    Trade,
    cluster_se,
    costs,
    ffill,
    net_return,
    sell_tax,
    simulate_trade,
)


def _cal(n: int) -> list[str]:
    return [f"2024{m:02d}{d:02d}" for m in range(1, 13) for d in range(1, 29)][:n]


def test_sell_tax_schedule_and_cost_floor() -> None:
    assert sell_tax("20190529") == 0.0030
    assert sell_tax("20190530") == 0.0025
    assert sell_tax("20250615") == 0.0015
    assert sell_tax("20260102") == 0.0020
    assert costs("20250102", "20250103") == (0.0025, 0.0025)  # 0.015% + 0.15% < 0.25%
    assert costs("20180102", "20180103") == (0.0025, 0.00315)
    assert costs("20250102", "20250103", real=True) == (0.00015, 0.00165)


def test_trade_fills_below_limit_and_exits_after_hold() -> None:
    cal = _cal(60)
    p: list[float | None] = [10_000.0] * 60
    p[11] = 10_100.0  # 첫날 종가 > 한도 10,050 → 미체결
    p[12] = 10_000.0  # 둘째 날 한도 = 10,100 × 1.005 내림 = 10,150 → 체결
    tr = simulate_trade(cal, p, ffill(p), 10)
    assert isinstance(tr, Trade)
    assert tr.fill == 12 and tr.entry == 10_150
    assert tr.exit_i == 12 + HOLD + 1  # 종가 10,000 > 한도 9,950
    assert tr.exit == 9_950 and not tr.delisted


def test_unfilled_after_five_tries_and_delisting_exit() -> None:
    cal = _cal(60)
    up: list[float | None] = [10_000.0 * 1.01**i for i in range(60)]
    assert simulate_trade(cal, up, ffill(up), 5) is None
    gone: list[float | None] = [10_000.0] * 20 + [None] * 40
    tr = simulate_trade(cal, gone, ffill(gone), 5)
    assert isinstance(tr, Trade) and tr.delisted and tr.exit_i == 19


def test_incomplete_when_calendar_ends() -> None:
    cal = _cal(30)
    p: list[float | None] = [10_000.0] * 30
    assert simulate_trade(cal, p, ffill(p), 10) == "incomplete"


def test_net_return_charges_both_sides() -> None:
    cal = _cal(40)
    tr = Trade(1, 100.0, 30, 110.0, False)
    assert math.isclose(net_return(tr, cal), 110 * (1 - 0.0025) / (100 * 1.0025) - 1)


def test_cluster_se_matches_iid_with_singleton_blocks_far_apart() -> None:
    xs = [0.01, -0.02, 0.03, 0.00, 0.015]
    se = cluster_se(xs, [0, 10, 20, 30, 40])
    assert math.isclose(se, statistics.pstdev(xs) / math.sqrt(len(xs)))
    assert cluster_se(xs, [0, 0, 0, 0, 0]) < 1e-12  # 한 묶음이면 합이 0
