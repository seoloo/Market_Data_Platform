"""Bias / setup / Trigger 합성, 비용 게이트 단위 테스트 (D14).

핵심 성질 두 가지를 여기서 못 박는다.

1. **변동성 국면은 방향 점수를 건드리지 않는다** — 크기만 줄인다. 이게 깨지면
   완충장치가 다시 거부권이 되어 고변동장에서 진입이 수학적으로 불가능해진다.
2. **T1 은 순서 조건이다** — 되돌림이 *먼저* 있어야 T2 방아쇠가 유효하다.
"""

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
from auto_trading.strategy.setup import SetupState, detect_setup
from auto_trading.strategy.trigger import TriggerResult, compute_trigger

D = Decimal
POLICY = PolicyConfig()
T1_CFG = FeatureConfig(
    timeframe=Timeframe.M15,
    weight=0.0,
    params={
        "long_zone": [35.0, 50.0],
        "short_zone": [50.0, 65.0],
        "invalidate_below": 30.0,
        "invalidate_above": 70.0,
        "setup_lookback_bars": 12,
    },
)
T2_CFG = FeatureConfig(
    timeframe=Timeframe.M5,
    weight=1.0,
    params={"min_abs_z": 0.3, "squash_k": 1.5},
)
B2_CFG = FeatureConfig(
    timeframe=Timeframe.H1,
    params={"high_percentile": 0.70, "high_risk_scale": 0.5},
)
ARMED = SetupState(armed=True, bars_since=0, depth_rsi=D("40"), reason="armed")
NOT_ARMED = SetupState(armed=False, bars_since=None, depth_rsi=None, reason="no_pullback")


def feature(name: str, timeframe: Timeframe, raw: str, normalized: float) -> FeatureResult:
    return FeatureResult(
        name=name, timeframe=timeframe, available=True, raw=D(raw), normalized=normalized
    )


def pullback(rsi_history: list[str]) -> FeatureResult:
    return FeatureResult(
        name="t1_pullback",
        timeframe=Timeframe.M15,
        available=True,
        raw=D(rsi_history[-1]),
        normalized=float((D(rsi_history[-1]) - 50) / 50),
        detail={"history": tuple(D(v) for v in rsi_history)},
    )


# --------------------------------------------------------------------- Bias


class TestBias:
    def test_high_volatility_scales_risk_not_direction(self) -> None:
        """고변동 국면은 **크기만** 줄인다. 방향 배수는 존재하지 않는다."""
        result = compute_bias(
            feature("b1_trend", Timeframe.H4, "1.2", 1.2),
            feature("b2_volregime", Timeframe.H1, "0.02", 0.8),
            POLICY,
            VolatilityGateConfig(),
            B2_CFG,
        )
        assert result == BiasResult(direction=1, risk_scale=D("0.5"), regime="high", available=True)
        assert not result.is_extreme

    def test_normal_regime_uses_full_risk(self) -> None:
        result = compute_bias(
            feature("b1_trend", Timeframe.H4, "1.2", 1.2),
            feature("b2_volregime", Timeframe.H1, "0.02", 0.5),
            POLICY,
            VolatilityGateConfig(),
            B2_CFG,
        )
        assert result.risk_scale == D(1)
        assert result.regime == "normal"

    @pytest.mark.parametrize("percentile", [0.049, 0.981])
    def test_extreme_regime_is_reported_not_silently_zeroed(self, percentile: float) -> None:
        """극단 국면은 점수를 0 으로 만들지 않고 게이트가 막는다 (책임 분리)."""
        result = compute_bias(
            feature("b1_trend", Timeframe.H4, "1.2", 1.2),
            feature("b2_volregime", Timeframe.H1, "0.02", percentile),
            POLICY,
            VolatilityGateConfig(),
            B2_CFG,
        )
        assert result.is_extreme
        assert result.risk_scale == D(1)  # 크기를 깎는 게 아니라 아예 거래를 막는 국면

    def test_weak_trend_gives_no_direction(self) -> None:
        result = compute_bias(
            feature("b1_trend", Timeframe.H4, "0.49", 0.49),
            feature("b2_volregime", Timeframe.H1, "0.02", 0.5),
            POLICY,
            VolatilityGateConfig(),
            B2_CFG,
        )
        assert result.direction == 0

    def test_unavailable_bias_fails_closed(self) -> None:
        result = compute_bias(
            FeatureResult.unavailable("b1_trend", Timeframe.H4, "missing"),
            feature("b2_volregime", Timeframe.H1, "0.02", 0.5),
            POLICY,
            VolatilityGateConfig(),
            B2_CFG,
        )
        assert not result.available
        assert result.direction == 0

    def test_risk_scale_outside_unit_interval_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="risk_scale"):
            BiasResult(direction=1, risk_scale=D(0), regime="normal", available=True)


# -------------------------------------------------------------------- Setup


class TestSetup:
    def test_recent_pullback_arms_the_setup(self) -> None:
        history = [D(v) for v in ("60", "55", "45", "52", "58")]  # 2봉 전 45 로 눌림
        state = detect_setup(history, 1, T1_CFG)
        assert state.armed
        assert state.bars_since == 2
        assert state.depth_rsi == D("45")

    def test_pullback_happening_right_now_also_arms(self) -> None:
        state = detect_setup([D("60"), D("58"), D("46")], 1, T1_CFG)
        assert state.armed
        assert state.bars_since == 0

    def test_no_pullback_in_window_leaves_setup_unarmed(self) -> None:
        state = detect_setup([D("60")] * 10, 1, T1_CFG)
        assert not state.armed
        assert state.reason == "no_pullback"

    def test_pullback_older_than_lookback_expires(self) -> None:
        cfg = FeatureConfig(
            timeframe=Timeframe.M15, params={**T1_CFG.params, "setup_lookback_bars": 3}
        )
        history = [D("45")] + [D("60")] * 5  # 눌림이 창 밖으로 밀려남
        assert not detect_setup(history, 1, cfg).armed

    def test_deep_break_invalidates_the_setup(self) -> None:
        """되돌림이 아니라 추세 이탈이면 무효다."""
        history = [D("45"), D("28"), D("55")]  # 되돌림 뒤 30 아래로 붕괴
        state = detect_setup(history, 1, T1_CFG)
        assert not state.armed
        assert state.reason == "invalidated"

    def test_short_side_is_mirrored(self) -> None:
        history = [D("40"), D("45"), D("58"), D("48")]  # 숏 zone [50,65] 에 58 진입
        state = detect_setup(history, -1, T1_CFG)
        assert state.armed
        assert state.bars_since == 1
        assert state.depth_rsi == D("58")

    def test_short_side_break_above_invalidates(self) -> None:
        # 되돌림(58) 뒤에 70 위로 이탈. 그 뒤 zone 재진입이 없어야 무효가 유지된다.
        state = detect_setup([D("58"), D("72"), D("80")], -1, T1_CFG)
        assert not state.armed
        assert state.reason == "invalidated"

    def test_reentering_the_zone_after_a_break_arms_a_fresh_setup(self) -> None:
        """이탈 후 다시 눌리면 그건 **새 setup** 이다 — 과거 이탈에 영원히 묶이지 않는다."""
        state = detect_setup([D("58"), D("72"), D("60")], -1, T1_CFG)
        assert state.armed
        assert state.bars_since == 0

    def test_no_bias_means_no_setup(self) -> None:
        state = detect_setup([D("45")], 0, T1_CFG)
        assert not state.armed
        assert state.reason == "no_bias"

    def test_empty_history_is_not_treated_as_absence_of_pullback(self) -> None:
        state = detect_setup([], 1, T1_CFG)
        assert state.reason == "no_history"

    def test_freshness_decays_with_age(self) -> None:
        fresh = SetupState(True, 0, D("40"), "armed")
        stale = SetupState(True, 9, D("40"), "armed")
        assert fresh.freshness(12) == pytest.approx(1.0)
        assert stale.freshness(12) == pytest.approx(0.25)
        assert NOT_ARMED.freshness(12) == 0.0

    def test_armed_setup_must_carry_its_age(self) -> None:
        with pytest.raises(ValueError, match="bars_since"):
            SetupState(armed=True, bars_since=None, depth_rsi=None, reason="armed")


# ------------------------------------------------------------------ Trigger


class TestTrigger:
    def test_armed_setup_lets_momentum_fire(self) -> None:
        result = compute_trigger(
            pullback(["60", "45", "55"]),
            feature("t2_momentum", Timeframe.M5, "0.01", 1.2),
            feature("t3_volume", Timeframe.M5, "1.3", 1.0),
            bias_direction=1,
            pullback_cfg=T1_CFG,
            momentum_cfg=T2_CFG,
        )
        assert result.setup.armed
        # squash(1.2, 1.5) = 1.2 / 2.7
        assert result.value == pytest.approx(1.2 / 2.7)

    def test_momentum_without_a_setup_does_not_fire(self) -> None:
        """되돌림이 없었으면 아무리 강한 모멘텀도 진입이 아니다 — D14 의 핵심."""
        result = compute_trigger(
            pullback(["60"] * 12),
            feature("t2_momentum", Timeframe.M5, "0.01", 5.0),
            feature("t3_volume", Timeframe.M5, "1.3", 1.0),
            bias_direction=1,
            pullback_cfg=T1_CFG,
            momentum_cfg=T2_CFG,
        )
        assert result.available
        assert not result.setup.armed
        assert result.value == 0.0
        assert result.momentum_value == pytest.approx(5.0)  # 값은 기록한다

    def test_short_side_is_symmetric(self) -> None:
        result = compute_trigger(
            pullback(["40", "58", "48"]),
            feature("t2_momentum", Timeframe.M5, "-0.01", -1.2),
            feature("t3_volume", Timeframe.M5, "1.3", 1.0),
            bias_direction=-1,
            pullback_cfg=T1_CFG,
            momentum_cfg=T2_CFG,
        )
        assert result.value == pytest.approx(-1.2 / 2.7)

    def test_deadband_suppresses_weak_momentum(self) -> None:
        result = compute_trigger(
            pullback(["60", "45", "55"]),
            feature("t2_momentum", Timeframe.M5, "0.001", 0.2),
            feature("t3_volume", Timeframe.M5, "1.3", 1.0),
            bias_direction=1,
            pullback_cfg=T1_CFG,
            momentum_cfg=T2_CFG,
        )
        assert result.momentum_value == 0.0
        assert result.value == 0.0

    def test_volume_confirmation_is_multiplicative(self) -> None:
        weak = compute_trigger(
            pullback(["60", "45", "55"]),
            feature("t2_momentum", Timeframe.M5, "0.01", 1.2),
            feature("t3_volume", Timeframe.M5, "0.7", 0.6),
            bias_direction=1,
            pullback_cfg=T1_CFG,
            momentum_cfg=T2_CFG,
        )
        assert weak.value == pytest.approx(1.2 / 2.7 * 0.6)

    def test_missing_feature_fails_closed(self) -> None:
        result = compute_trigger(
            FeatureResult.unavailable("t1_pullback", Timeframe.M15, "missing"),
            feature("t2_momentum", Timeframe.M5, "0", 1.0),
            feature("t3_volume", Timeframe.M5, "1", 1.0),
            bias_direction=1,
            pullback_cfg=T1_CFG,
            momentum_cfg=T2_CFG,
        )
        assert not result.available

    def test_missing_rsi_history_fails_closed_rather_than_assuming_no_pullback(self) -> None:
        bare = feature("t1_pullback", Timeframe.M15, "45", -0.1)  # detail 없음
        result = compute_trigger(
            bare,
            feature("t2_momentum", Timeframe.M5, "0", 1.0),
            feature("t3_volume", Timeframe.M5, "1", 1.0),
            bias_direction=1,
            pullback_cfg=T1_CFG,
            momentum_cfg=T2_CFG,
        )
        assert not result.available


# ------------------------------------------------------------------ Scoring


class TestScoring:
    def test_high_volatility_does_not_lower_the_score(self) -> None:
        """같은 trigger 면 국면이 달라도 점수가 같아야 한다 (D14 회귀 방지)."""
        trigger = TriggerResult(0.4, 1.2, 1.0, ARMED, available=True)
        normal = compose_scores(
            BiasResult(1, D(1), "normal", True), trigger, POLICY, volatility_percentile=0.5
        )
        high = compose_scores(
            BiasResult(1, D("0.5"), "high", True), trigger, POLICY, volatility_percentile=0.8
        )
        assert normal.long_score == pytest.approx(70.0)
        assert high.long_score == pytest.approx(70.0)

    def test_score_is_symmetric_around_fifty(self) -> None:
        scores = compose_scores(
            BiasResult(1, D(1), "normal", True),
            TriggerResult(0.4, 1.2, 1.0, ARMED, available=True),
            POLICY,
        )
        assert scores.long_score + scores.short_score == pytest.approx(100.0)

    def test_opposite_trigger_is_blocked_by_bias(self) -> None:
        scores = compose_scores(
            BiasResult(1, D(1), "normal", True),
            TriggerResult(-0.4, -2.0, 1.0, ARMED, available=True),
            POLICY,
        )
        assert scores.direction == 0.0

    def test_unavailable_bias_is_neutral(self) -> None:
        scores = compose_scores(
            BiasResult(0, D(1), "unavailable", False),
            TriggerResult(0.4, 1.2, 1.0, ARMED, available=True),
            POLICY,
        )
        assert scores == scores.neutral()

    def test_neutral_bias_can_be_allowed_at_half_weight(self) -> None:
        scores = compose_scores(
            BiasResult(0, D(1), "normal", True),
            TriggerResult(0.4, 1.2, 1.0, ARMED, available=True),
            PolicyConfig(allow_counter_bias_when_neutral=True),
        )
        assert scores.direction == pytest.approx(0.2)

    def test_confidence_reflects_setup_freshness(self) -> None:
        fresh = compose_scores(
            BiasResult(1, D(1), "normal", True),
            TriggerResult(0.4, 1.2, 1.0, SetupState(True, 0, D("40"), "armed"), available=True),
            POLICY,
            setup_lookback_bars=12,
        )
        stale = compose_scores(
            BiasResult(1, D(1), "normal", True),
            TriggerResult(0.4, 1.2, 1.0, SetupState(True, 9, D("40"), "armed"), available=True),
            POLICY,
            setup_lookback_bars=12,
        )
        assert fresh.confidence_score > stale.confidence_score
        assert fresh.long_score == stale.long_score  # 진입 판정에는 영향이 없다


# --------------------------------------------------------------------- Cost


def test_cost_is_round_trip_plus_adverse_funding() -> None:
    result = estimate_cost(CostConfig(), side=PositionSide.LONG, funding_rate=D("0.0001"))
    assert result.fee_bps == D("9.00000")
    assert result.slippage_bps == D("4.00000")
    assert result.funding_bps == D("1.0000")
    assert result.total_bps == D("14.00000")


def test_favorable_funding_is_not_counted_as_negative_cost() -> None:
    result = estimate_cost(CostConfig(), side=PositionSide.SHORT, funding_rate=D("0.0001"))
    assert result.funding_bps == D("0")
    assert result.total_bps == D("13.00000")


def test_edge_requires_thirty_train_samples() -> None:
    assert not estimate_edge(D("30"), 29).available
    assert estimate_edge(D("30"), 30).available
    assert not estimate_edge(None, 100).available
