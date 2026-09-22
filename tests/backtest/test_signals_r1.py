from __future__ import annotations

import pytest

from auto_trading.backtest.signals_r1 import (
    DAY_MS,
    R1Observation,
    R1Result,
    cumulative_funding_bps,
    evaluate_r1,
    momentum_signal,
    r1_verdict,
    sample_return,
)


def observation(at_ms: int, *, slope: float = 10.0) -> R1Observation:
    symbols = [f"S{index:02d}" for index in range(10)]
    return R1Observation(
        at_ms=at_ms,
        signal={symbol: float(index) for index, symbol in enumerate(symbols)},
        price_forward_bps={symbol: slope * index for index, symbol in enumerate(symbols)},
        funding_rate_bps=dict.fromkeys(symbols, 0.0),
    )


@pytest.mark.leakage
def test_momentum_uses_only_closes_before_entry() -> None:
    entry = 100 * DAY_MS
    closes = {entry - 22 * DAY_MS: 100.0, entry - DAY_MS: 121.0, entry: 9_999.0}
    assert momentum_signal(closes, entry) == pytest.approx(0.21)
    closes[entry] = -1.0
    assert momentum_signal(closes, entry) == pytest.approx(0.21)


def test_funding_excludes_entry_and_exit_boundaries() -> None:
    rates = {0: 0.1, 1: 0.001, 2: 0.002, 3: 0.1}
    assert cumulative_funding_bps(rates, 0, 3) == pytest.approx(30.0)


def test_missing_funding_is_not_treated_as_zero() -> None:
    assert cumulative_funding_bps({}, 0, DAY_MS) is None


def test_sample_accounts_for_long_payment_short_receipt_and_cost() -> None:
    row = observation(0)
    funding = dict(row.funding_rate_bps)
    for index in range(3):
        funding[f"S{index:02d}"] = 4.0
        funding[f"S{index + 7:02d}"] = 10.0
    row = R1Observation(row.at_ms, row.signal, row.price_forward_bps, funding)
    sample = sample_return(row, cost_bps=22.0)
    assert sample is not None
    assert sample.gross_bps == pytest.approx(35.0)
    assert sample.funding_bps == pytest.approx(-3.0)
    assert sample.net_bps == pytest.approx(10.0)


def test_evaluate_removes_overlapping_samples() -> None:
    rows = [observation(index * DAY_MS) for index in range(12)]
    result = evaluate_r1("3d", rows, embargo_ms=3 * DAY_MS)
    assert result.sample_count == 4


def result(
    horizon: str,
    *,
    count: int = 300,
    mean: float = 20.0,
    stderr: float = 2.0,
    quantiles: tuple[float, ...] = (-20.0, -10.0, 0.0, 10.0, 20.0),
    yearly: dict[int, float] | None = None,
) -> R1Result:
    return R1Result(
        horizon,
        count,
        mean + 22.0,
        0.0,
        mean,
        stderr,
        0.6,
        quantiles,
        yearly or {2023: 10.0, 2024: 30.0},
    )


def test_preregistered_verdict_passes_only_combined_conditions() -> None:
    passed, reasons = r1_verdict([result("1d"), result("3d")])
    assert passed
    assert reasons == ()


def test_preregistered_verdict_rejects_negative_peer_horizon() -> None:
    passed, reasons = r1_verdict([result("1d"), result("3d", mean=-1.0)])
    assert not passed
    assert "HORIZON_SIGN" in reasons


def test_preregistered_verdict_requires_both_years_positive() -> None:
    bad_years = {2023: 20.0, 2024: -1.0}
    passed, reasons = r1_verdict([result("1d", yearly=bad_years), result("3d", yearly=bad_years)])
    assert not passed
    assert "YEAR_SIGN" in reasons
