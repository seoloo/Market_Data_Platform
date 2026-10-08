from __future__ import annotations

import pytest
from scripts.run_div_mix_180 import QQQ_FROM, _window_stats, ndx_total


def test_ndx_total_splices_price_plus_div_then_qqq() -> None:
    table = {"1999-03": {"NDX": 0.01}, "1999-04": {"NDX": 0.02}, QQQ_FROM: {"NDX": 0.5}}
    out = ndx_total(table, {QQQ_FROM: 0.03, "1999-04": 9.9}, 0.012)
    assert out["1999-03"] == pytest.approx(0.011)
    assert out["1999-04"] == pytest.approx(0.021)
    assert out[QQQ_FROM] == pytest.approx(0.03)


def test_window_stats_total_and_drawdown() -> None:
    w = _window_stats([0.10, -0.20, 0.05])
    assert w["total"] == pytest.approx(round(1.1 * 0.8 * 1.05 - 1, 4))
    assert w["mdd"] == pytest.approx(0.2)
