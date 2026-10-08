from __future__ import annotations

from scripts.forward_band import grade, window_stats


def test_window_stats_on_steady_growth() -> None:
    eq = [100 * 1.01**i for i in range(50)]
    st = window_stats(eq, 10)
    assert st is not None
    assert abs(st["ret_p05"] - (1.01**10 - 1)) < 1e-9
    assert st["mdd_p99"] == 0.0
    assert window_stats(eq, 60) is None


def test_grade_flags_paths_outside_backtest_range() -> None:
    bt = [100 * (1.01 if i % 2 else 0.995) ** 1 * (1.002**i) for i in range(400)]
    ok = [bt[0] * 1.002**i for i in range(11)]
    assert grade(ok, bt)["grade"] in ("OK", "REVIEW")
    crash = [100.0, 95.0, 90.0, 85.0, 80.0, 75.0, 70.0, 68.0, 66.0, 64.0, 62.0]
    assert grade(crash, bt)["grade"] == "ALERT"
    assert grade([100.0], bt)["grade"] == "NA"
