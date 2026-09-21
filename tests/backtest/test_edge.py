"""TRAIN edge table 추정과 fail-closed 규칙."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.backtest.edge import EdgeObservation, estimate_edge_table, forward_return_bps
from auto_trading.core.enums import PositionSide, Timeframe

D = Decimal


def observation(
    value: str,
    *,
    side: PositionSide = PositionSide.LONG,
    score: float = 75.0,
    regime: str = "normal",
    horizon: int = 24,
) -> EdgeObservation:
    return EdgeObservation(
        as_of_ms=1,
        side=side,
        score=score,
        regime=regime,
        horizon_bars=horizon,
        gross_return_bps=D(value),
    )


def table(items: tuple[EdgeObservation, ...], *, min_samples: int = 30):
    return estimate_edge_table(
        items,
        symbol="BTCUSDT",
        source_config_hash="test-config",
        train_from_ms=1,
        train_to_ms=2,
        decision_timeframe=Timeframe.M5,
        min_samples=min_samples,
    )


def test_forward_return_is_direction_symmetric() -> None:
    assert forward_return_bps(PositionSide.LONG, D("100"), D("101")) == D("100")
    assert forward_return_bps(PositionSide.SHORT, D("100"), D("101")) == D("-100")


def test_bucket_mean_and_positive_rate_are_exact_decimals() -> None:
    result = table((observation("10"), observation("-5"), observation("25")), min_samples=3)
    bucket = result.buckets[0]
    assert bucket.observed_mean_bps == D("10")
    assert bucket.positive_rate == D(2) / D(3)
    assert bucket.available
    assert bucket.edge_bps == D("10")


def test_fewer_than_thirty_samples_fail_closed_to_zero() -> None:
    result = table(tuple(observation("100") for _ in range(29)))
    bucket = result.buckets[0]
    assert bucket.observed_mean_bps == D("100")
    assert not bucket.available
    assert bucket.edge_bps == D(0)


def test_score_boundary_is_left_closed_and_last_bucket_includes_100() -> None:
    result = table(
        (
            observation("1", score=79.999),
            observation("2", score=80.0),
            observation("3", score=100.0),
        ),
        min_samples=1,
    )
    assert [
        (bucket.score_from, bucket.score_to, bucket.sample_count) for bucket in result.buckets
    ] == [
        (70.0, 80.0, 1),
        (80.0, 90.0, 1),
        (90.0, 100.0, 1),
    ]


def test_lookup_separates_side_regime_and_horizon() -> None:
    result = table(
        (
            observation("10"),
            observation("20", side=PositionSide.SHORT),
            observation("30", regime="high"),
            observation("40", horizon=72),
        ),
        min_samples=1,
    )
    found = result.lookup(
        side=PositionSide.SHORT,
        score=75.0,
        regime="normal",
        horizon_bars=24,
    )
    assert found is not None
    assert found.edge_bps == D("20")
    assert (
        result.lookup(
            side=PositionSide.LONG,
            score=69.0,
            regime="normal",
            horizon_bars=24,
        )
        is None
    )


def test_digest_and_json_are_independent_of_input_order() -> None:
    items = (observation("10"), observation("20", side=PositionSide.SHORT))
    first = table(items, min_samples=1)
    second = table(tuple(reversed(items)), min_samples=1)
    assert first.digest == second.digest
    assert first.to_json() == second.to_json()


def test_invalid_boundaries_are_rejected() -> None:
    with pytest.raises(ValueError, match="오름차순"):
        estimate_edge_table(
            (observation("1"),),
            symbol="BTCUSDT",
            source_config_hash="test-config",
            train_from_ms=1,
            train_to_ms=2,
            decision_timeframe=Timeframe.M5,
            score_boundaries=(70.0, 90.0, 80.0),
        )


def test_requested_horizons_survive_when_there_are_no_signals() -> None:
    result = estimate_edge_table(
        (),
        symbol="BTCUSDT",
        source_config_hash="test-config",
        train_from_ms=1,
        train_to_ms=2,
        decision_timeframe=Timeframe.M5,
        horizons_bars=(24, 72),
    )
    assert result.horizons_bars == (24, 72)
    assert result.observations == 0
    assert not result.buckets
    assert '"source_config_hash": "test-config"' in result.to_json()
