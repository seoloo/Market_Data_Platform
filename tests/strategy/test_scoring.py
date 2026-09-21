"""Bias/Trigger 합성, 비용 게이트 단위 테스트."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.config.schema import (
    CostConfig,
    FeatureConfig,
    PolicyConfig,
    VolatilityGateConfig,
)
from auto_trading.core.enums import PositionSide, Timeframe
from auto_trading.features.base import FeatureResult
from auto_trading.strategy.bias import BiasResult, compute_bias
from auto_trading.strategy.cost import estimate_cost, estimate_edge
from auto_trading.strategy.scoring import compose_scores
from auto_trading.strategy.trigger import TriggerResult, compute_trigger

D = Decimal
POLICY = PolicyConfig()
T1_CFG = FeatureConfig(
    timeframe=Timeframe.M15,
    weight=0.5,
    params={
        "long_zone": [35.0, 50.0],
        "short_zone": [50.0, 65.0],
        "invalidate_below": 30.0,
        "invalidate_above": 70.0,
    },
)
T2_CFG = FeatureConfig(
    timeframe=Timeframe.M5,
    weight=0.5,
    params={"min_abs_z": 0.3, "squash_k": 1.5},
)


def feature(name: str, timeframe: Timeframe, raw: str, normalized: float) -> FeatureResult:
    return FeatureResult(
        name=name,
        timeframe=timeframe,
        available=True,
        raw=D(raw),
        normalized=normalized,
    )


def test_bias_uses_trend_direction_and_volatility_multiplier() -> None:
    result = compute_bias(
        feature("b1_trend", Timeframe.H4, "1.2", 1.2),
        feature("b2_volregime", Timeframe.H1, "0.02", 0.8),
        POLICY,
        VolatilityGateConfig(),
    )
    assert result == BiasResult(direction=1, multiplier=0.5, regime="high", available=True)


def test_unavailable_bias_fails_closed() -> None:
    missing = FeatureResult.unavailable("b1_trend", Timeframe.H4, "missing")
    result = compute_bias(
        missing,
        feature("b2_volregime", Timeframe.H1, "0.02", 0.5),
        POLICY,
        VolatilityGateConfig(),
    )
    assert not result.available
    assert result.direction == 0
    assert result.multiplier == 0.0


def test_weak_trend_is_neutral_normal_regime() -> None:
    result = compute_bias(
        feature("b1_trend", Timeframe.H4, "0.49", 0.49),
        feature("b2_volregime", Timeframe.H1, "0.02", 0.5),
        POLICY,
        VolatilityGateConfig(),
    )
    assert result.direction == 0
    assert result.multiplier == 1.0
    assert result.regime == "normal"


@pytest.mark.parametrize("percentile", [0.049, 0.981])
def test_extreme_volatility_closes_bias_gate(percentile: float) -> None:
    result = compute_bias(
        feature("b1_trend", Timeframe.H4, "1.2", 1.2),
        feature("b2_volregime", Timeframe.H1, "0.02", percentile),
        POLICY,
        VolatilityGateConfig(),
    )
    assert result.multiplier == 0.0
    assert result.regime == "extreme"


def test_trigger_squashes_once_so_entry_threshold_is_reachable() -> None:
    result = compute_trigger(
        feature("t1_pullback", Timeframe.M15, "38", -0.24),
        feature("t2_momentum", Timeframe.M5, "0.01", 1.2),
        feature("t3_volume", Timeframe.M5, "1.3", 1.0),
        bias_direction=1,
        pullback_cfg=T1_CFG,
        momentum_cfg=T2_CFG,
    )
    # RSI 38 -> T1 0.8. (0.5*0.8 + 0.5*1.2) = 1.0; squash(1.0)=0.4.
    assert result.pullback_signal == pytest.approx(0.8)
    assert result.momentum_value == pytest.approx(1.2)
    assert result.value == pytest.approx(0.4)


def test_short_pullback_is_symmetric() -> None:
    result = compute_trigger(
        feature("t1_pullback", Timeframe.M15, "62", 0.24),
        feature("t2_momentum", Timeframe.M5, "-0.01", -1.2),
        feature("t3_volume", Timeframe.M5, "1.3", 1.0),
        bias_direction=-1,
        pullback_cfg=T1_CFG,
        momentum_cfg=T2_CFG,
    )
    assert result.pullback_signal == pytest.approx(-0.8)
    assert result.value == pytest.approx(-0.4)


def test_trigger_unavailable_and_zero_weight_fail_closed() -> None:
    missing = FeatureResult.unavailable("t1_pullback", Timeframe.M15, "missing")
    unavailable = compute_trigger(
        missing,
        feature("t2_momentum", Timeframe.M5, "0", 1.0),
        feature("t3_volume", Timeframe.M5, "1", 1.0),
        bias_direction=1,
        pullback_cfg=T1_CFG,
        momentum_cfg=T2_CFG,
    )
    assert not unavailable.available

    zero_weight = FeatureConfig(timeframe=Timeframe.M15, weight=0.0)
    no_weight = compute_trigger(
        feature("t1_pullback", Timeframe.M15, "38", -0.24),
        feature("t2_momentum", Timeframe.M5, "0", 1.0),
        feature("t3_volume", Timeframe.M5, "1", 1.0),
        bias_direction=1,
        pullback_cfg=zero_weight,
        momentum_cfg=FeatureConfig(timeframe=Timeframe.M5, weight=0.0),
    )
    assert not no_weight.available


def test_same_trigger_reaches_long_score_seventy() -> None:
    scores = compose_scores(
        BiasResult(direction=1, multiplier=1.0, regime="normal", available=True),
        TriggerResult(0.4, 0.8, 1.2, 1.0, available=True),
        POLICY,
        volatility_percentile=0.5,
    )
    assert scores.long_score == pytest.approx(70.0)
    assert scores.short_score == pytest.approx(30.0)


def test_opposite_trigger_is_blocked_by_bias() -> None:
    scores = compose_scores(
        BiasResult(direction=1, multiplier=1.0, regime="normal", available=True),
        TriggerResult(-0.4, 0.8, -2.0, 1.0, available=True),
        POLICY,
    )
    assert scores.direction == 0.0


def test_unavailable_and_neutral_bias_scoring_paths() -> None:
    trigger = TriggerResult(0.4, 0.8, 1.2, 1.0, available=True)
    unavailable = compose_scores(
        BiasResult(direction=0, multiplier=0.0, regime="unavailable", available=False),
        trigger,
        POLICY,
    )
    assert unavailable == unavailable.neutral()

    counter = compose_scores(
        BiasResult(direction=0, multiplier=1.0, regime="normal", available=True),
        trigger,
        PolicyConfig(allow_counter_bias_when_neutral=True),
    )
    assert counter.direction == pytest.approx(0.2)


def test_cost_is_round_trip_plus_adverse_funding() -> None:
    result = estimate_cost(
        CostConfig(),
        side=PositionSide.LONG,
        funding_rate=D("0.0001"),
    )
    assert result.fee_bps == D("9.00000")
    assert result.slippage_bps == D("4.00000")
    assert result.funding_bps == D("1.0000")
    assert result.total_bps == D("14.00000")


def test_favorable_funding_is_not_counted_as_negative_cost() -> None:
    result = estimate_cost(
        CostConfig(),
        side=PositionSide.SHORT,
        funding_rate=D("0.0001"),
    )
    assert result.funding_bps == D("0")
    assert result.total_bps == D("13.00000")


def test_edge_requires_thirty_train_samples() -> None:
    assert not estimate_edge(D("30"), 29).available
    assert estimate_edge(D("30"), 30).available
    assert not estimate_edge(None, 100).available
