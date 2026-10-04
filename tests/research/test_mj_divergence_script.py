from __future__ import annotations

import pytest
from scripts.run_mj_divergence import account, streak

from auto_trading.backtest.ema_box import Bars
from auto_trading.backtest.mj_divergence import Trade

H = 3_600_000


def _bars(n: int) -> Bars:
    t = [k * H for k in range(n)]
    one = [1.0] * n
    return Bars(t, one, one, one, one, one)


def _trade(entry_i: int, exit_i: int, r: float) -> Trade:
    return Trade(1, entry_i - 1, entry_i, exit_i, 100.0, 98.0, r, "stop")


def test_account_same_bar_trade_and_compounding() -> None:
    bars = {"X": _bars(10)}
    rows = [("X", _trade(1, 1, -1.0)), ("X", _trade(2, 4, 1.5))]
    acc = account(rows, bars)
    assert acc["final"] == pytest.approx(100 * 0.95 * 1.075)
    assert acc["mdd"] == pytest.approx(0.05)


def test_streak_counts_non_positive() -> None:
    assert streak([1.0, -1.0, 0.0, -0.5, 2.0, -1.0]) == 3
