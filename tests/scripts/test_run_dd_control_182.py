from __future__ import annotations

import pytest
from scripts.run_dd_control_182 import (
    LEV_MAX,
    Signals,
    _prev_map,
    month_signals,
    rule_m1,
    rule_m2,
    rule_m3,
    run_rule,
)


def test_month_signals_trend_and_vol() -> None:
    days = [f"2020-{1 + i // 200:02d}-{i % 200:03d}" for i in range(130)]
    px = {d: 100.0 * 1.001**i for i, d in enumerate(days)}
    s = month_signals(days, px)["2020-01"]
    assert s.trend_on is True
    assert s.vol20 == pytest.approx(0.0, abs=1e-9)


def test_rules() -> None:
    assert rule_m1(Signals(False, 0.2), []) == 0.0
    assert rule_m1(Signals(None, None), []) == LEV_MAX
    assert rule_m2(None, [1.0, 0.85]) == pytest.approx(LEV_MAX * 0.5)
    assert rule_m2(None, [1.0, 0.6]) == 0.0
    assert rule_m3(Signals(True, 0.4), []) == pytest.approx(LEV_MAX * 0.5)
    assert rule_m3(Signals(True, 0.1), []) == LEV_MAX


def test_prev_map_and_run_rule_uses_last_month_signal() -> None:
    months = ["2020-01", "2020-02"]
    assert _prev_map(months) == {"2020-01": "2019-12", "2020-02": "2020-01"}
    sigs = {"2019-12": Signals(True, 0.1), "2020-01": Signals(False, 0.1)}
    rets, shares = run_rule(
        months,
        {"2020-01": 0.1, "2020-02": -0.3},
        {"2020-01": 0.0, "2020-02": 0.0},
        {"2020-01": 0.0, "2020-02": 0.0},
        sigs,
        _prev_map(months),
        rule_m1,
    )
    assert shares == [LEV_MAX, 0.0]
    assert rets[1] < 0 and rets[1] > -0.01  # 꺼진 달은 레버리지 0 — 회전 비용만
