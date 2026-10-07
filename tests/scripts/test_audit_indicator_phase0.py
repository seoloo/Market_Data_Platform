from __future__ import annotations

from scripts.audit_indicator_phase0 import event_power, gate_power, window_stats


def test_window_stats_excess_and_limit_filter() -> None:
    cal = ["d0", "d1", "d2"]
    series = {s: {"d0": 100.0, "d1": 100.0 * (1 + 0.01 * k)} for k, s in enumerate("abcdef")}
    series["bad"] = {"d0": 1.0, "d1": 50.0}
    out = window_stats(series, cal, 1, daily_limit=0.30)
    assert out["windows"] == 1
    assert out["stocks_per_window"] == 6
    assert out["implausible_dropped"] == 1
    assert abs(out["sd_excess"] - 0.0171) < 1e-4


def test_window_stats_respects_eligibility() -> None:
    cal = ["d0", "d1"]
    series = {s: {"d0": 1.0, "d1": 1.1} for s in "abcdefg"}
    out = window_stats(series, cal, 1, eligible=lambda _d: {"a", "b"})
    assert out["windows"] == 0


def test_power_needs_grow_with_sd_and_shrink_with_effect() -> None:
    small, big = event_power(0.1), event_power(0.2)
    assert small["0.5%"] > small["2.0%"]
    assert big["1.0%"] > small["1.0%"]
    rows = gate_power(0.15, 365)
    assert rows[0]["years_needed"]["2%/yr"] > rows[0]["years_needed"]["4%/yr"]
    assert rows[-1]["sd_daily_diff"] > rows[0]["sd_daily_diff"]
