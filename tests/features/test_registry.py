"""feature 조립 단일 지점 테스트.

``decide()`` 와 ``SnapshotBuilder`` 가 같은 정의를 쓴다는 것이 이 모듈의 존재 이유다.
어긋나면 "백테스트는 되는데 실거래에서만 warm-up 미충족" 이 된다.
"""

from __future__ import annotations

import pytest

from auto_trading.config.schema import FeatureConfig, FeaturesConfig, RootConfig
from auto_trading.core.enums import Timeframe
from auto_trading.features.registry import build_feature_set, float_param, int_param
from auto_trading.strategy.decision import _compute_features


def features_config(**params: dict[str, object]) -> FeaturesConfig:
    base: dict[str, dict[str, object]] = {
        "b1_trend": {"timeframe": "4h", "params": {"ema_period": 5, "atr_period": 3}},
        "b2_volregime": {"timeframe": "1h", "params": {"atr_period": 3, "lookback_days": 1}},
        "t1_pullback": {"timeframe": "15m", "params": {"rsi_period": 3}},
        "t2_momentum": {"timeframe": "5m", "params": {"return_bars": 2, "zscore_lookback": 6}},
        "t3_volume": {"timeframe": "5m", "params": {"sma_period": 3}},
    }
    base.update(params)
    return FeaturesConfig.model_validate(base)


class TestBuildFeatureSet:
    def test_parameters_reach_the_feature_objects(self) -> None:
        fset = build_feature_set(features_config())
        assert fset.trend.ema_period == 5
        assert fset.volatility.lookback_days == 1
        assert fset.pullback.rsi_period == 3
        assert fset.momentum.zscore_lookback == 6
        assert fset.volume.sma_period == 3

    def test_order_is_bias_then_trigger(self) -> None:
        names = [f.name for f in build_feature_set(features_config()).as_tuple()]
        assert names == ["b1_trend", "b2_volregime", "t1_pullback", "t2_momentum", "t3_volume"]

    def test_defaults_apply_when_params_are_absent(self) -> None:
        bare = FeaturesConfig.model_validate(
            {
                "b1_trend": {"timeframe": "4h"},
                "b2_volregime": {"timeframe": "1h"},
                "t1_pullback": {"timeframe": "15m"},
                "t2_momentum": {"timeframe": "5m"},
                "t3_volume": {"timeframe": "5m"},
            }
        )
        fset = build_feature_set(bare)
        assert fset.trend.ema_period == 50
        assert fset.pullback.rsi_period == 14


class TestWarmup:
    def test_shared_timeframe_takes_the_deepest_requirement(self) -> None:
        # 5m 을 T2(2+6=8)와 T3(sma 3)가 함께 쓴다.
        warmup = build_feature_set(features_config()).warmup_by_timeframe()
        assert warmup[Timeframe.M5] == 8

    def test_deeper_parameters_raise_the_requirement(self) -> None:
        shallow = build_feature_set(features_config()).warmup_by_timeframe()
        deep = build_feature_set(
            features_config(t1_pullback={"timeframe": "15m", "params": {"rsi_period": 30}})
        ).warmup_by_timeframe()
        assert deep[Timeframe.M15] > shallow[Timeframe.M15]

    def test_every_timeframe_in_use_is_covered(self) -> None:
        fset = build_feature_set(features_config())
        warmup = fset.warmup_by_timeframe()
        assert {f.timeframe for f in fset.as_tuple()} == set(warmup)


class TestSharedWithDecide:
    def test_decide_uses_the_same_definitions(self) -> None:
        """``decide()`` 가 별도로 feature 를 만들면 이 테스트가 깨진다."""
        cfg = RootConfig.model_validate({"features": features_config().model_dump(mode="json")})
        expected = [f.name for f in build_feature_set(cfg.features).as_tuple()]

        from tests.strategy.test_decision import snapshot

        computed = [result.name for result in _compute_features(snapshot(), cfg)]
        assert computed == expected


class TestParamCoercion:
    def test_list_is_rejected_where_a_number_is_required(self) -> None:
        cfg = FeatureConfig(timeframe=Timeframe.M5, params={"x": [1.0, 2.0]})
        with pytest.raises(ValueError, match="정수여야"):
            int_param(cfg, "x", 1)
        with pytest.raises(ValueError, match="숫자여야"):
            float_param(cfg, "x", 1.0)

    def test_missing_param_falls_back_to_default(self) -> None:
        cfg = FeatureConfig(timeframe=Timeframe.M5, params={})
        assert int_param(cfg, "missing", 7) == 7
        assert float_param(cfg, "missing", 0.5) == 0.5
