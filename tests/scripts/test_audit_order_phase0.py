from __future__ import annotations

from scripts.audit_order_phase0 import min_r, needed_n, power_rows, window_moments


def test_min_r_and_needed_n_are_inverse() -> None:
    assert abs(min_r(needed_n(0.01, 2.0), 2.0) - 0.01) < 1e-12
    assert min_r(1000, 3.0) > min_r(1000, 1.0) > min_r(10_000, 1.0)


def test_window_moments_on_simple_panel() -> None:
    days = [f"d{k:02d}" for k in range(11)]
    series = {
        s: {d: 100.0 * (1 + 0.01 * j) ** (i // 5) for i, d in enumerate(days)}
        for j, s in enumerate("abcdef")
    }
    m = window_moments(series, days, lambda _d: set(series), h=5)
    assert m["windows"] == 2 and m["pairs_per_window"] == 6
    assert m["sd_excess"] > 0
    rows = power_rows(m, 1.8)
    assert rows["inflation_h"] == 1.0
    assert rows["min_incremental_r2_low"] > rows["min_incremental_r2_high"]
