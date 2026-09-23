"""설정 스키마 테스트.

설정 오타는 Tier 1 위험이다 (09-failure-modes.md F5).
특히 **파라미터 간 정합성 검증**(D9-3)은 값 범위 검증으로는 절대 잡히지 않으므로
집중적으로 검증한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from auto_trading.config.schema import (
    CostConfig,
    EdgeTableConfig,
    PolicyConfig,
    RiskConfig,
    RootConfig,
    UiConfig,
    VolatilityGateConfig,
)
from auto_trading.core.enums import Mode, PositionSide, Timeframe
from auto_trading.core.hashing import sha256_hex

D = Decimal

FEATURES = {
    "b1_trend": {"timeframe": "4h"},
    "b2_volregime": {"timeframe": "1h"},
    "t1_pullback": {"timeframe": "15m"},
    "t2_momentum": {"timeframe": "5m"},
    "t3_volume": {"timeframe": "5m", "weight": 0.0},
}


def root(**overrides) -> RootConfig:
    data = {"features": FEATURES, **overrides}
    return RootConfig.model_validate(data)


def edge_table_payload(*, missing_horizon: int | None = None) -> dict[str, object]:
    horizons = (24, 72, 144, 288)
    means = {24: "31", 72: "27", 144: "22", 288: "25"}
    buckets = [
        {
            "side": "LONG",
            "regime": "normal",
            "horizon_bars": horizon,
            "score_from": 70.0,
            "score_to": 80.0,
            "sample_count": 40,
            "observed_mean_bps": means[horizon],
            "positive_rate": "0.6",
            "min_samples": 30,
            "available": True,
            "edge_bps": means[horizon],
        }
        for horizon in horizons
        if horizon != missing_horizon
    ]
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "source_config_hash": "a" * 64,
        "train_from_ms": 1,
        "train_to_ms": 2,
        "decision_timeframe": "5m",
        "score_boundaries": [70.0, 80.0, 90.0, 100.0],
        "horizons_bars": list(horizons),
        "min_samples": 30,
        "observations": 160,
        "buckets": buckets,
    }
    payload["digest"] = sha256_hex(payload)
    payload["aggregation"] = "all_horizons_min"
    return payload


class TestDecimalPrecision:
    """YAML 의 float 가 Decimal 로 들어올 때 오차가 생기면 사이징이 틀어진다."""

    def test_float_becomes_exact_decimal(self):
        cfg = RiskConfig.model_validate({"risk_per_trade": 0.01})
        assert cfg.risk_per_trade == D("0.01")
        assert str(cfg.risk_per_trade) == "0.01"

    def test_naive_decimal_conversion_would_be_wrong(self):
        """이 테스트는 우리가 피하고 있는 문제를 문서화한다."""
        assert D(0.01) != D("0.01")  # 직접 변환은 오차가 생긴다
        assert RiskConfig.model_validate({"risk_per_trade": 0.01}).risk_per_trade == D("0.01")

    def test_string_input_also_works(self):
        assert RiskConfig.model_validate({"risk_per_trade": "0.01"}).risk_per_trade == D("0.01")

    def test_arithmetic_is_exact(self):
        c = CostConfig()
        # 0.00045 + 0.00020 = 0.00065 정확히
        assert c.cost_rate_in == D("0.00065")
        assert c.cost_rate_out == D("0.00085")
        assert c.fixed_cost_rate == D("0.0015")


class TestExtraKeysForbidden:
    """오타난 키가 조용히 무시되는 것이 가장 위험하다."""

    def test_typo_in_key_is_rejected(self):
        with pytest.raises(ValidationError, match="max_leverag"):
            RiskConfig.model_validate({"max_leverag": 5})  # 'e' 누락

    def test_unknown_nested_key_is_rejected(self):
        with pytest.raises(ValidationError):
            RiskConfig.model_validate({"cost": {"taker_fee": 0.001}})  # _rate 누락

    def test_unknown_root_key_is_rejected(self):
        with pytest.raises(ValidationError):
            root(unknown_section={"a": 1})


class TestImmutability:
    def test_config_is_frozen(self):
        cfg = RiskConfig()
        with pytest.raises(ValidationError):
            cfg.max_leverage = 20


class TestRangeValidation:
    @pytest.mark.parametrize(
        ("field", "bad_value"),
        [
            ("risk_per_trade", 0.5),  # 50% — 오타로 흔한 값
            ("risk_per_trade", 0),
            ("risk_per_trade", -0.01),
            ("max_leverage", 100),  # 명세 예시에 있던 위험한 값
            ("max_leverage", 11),  # 사용자 지정 범위 1x ~ 10x 초과
            ("max_leverage", 0),
            ("margin_allocation", 1.5),
            ("max_drawdown_pct", 0.9),
            ("min_stop_distance", 0),
        ],
    )
    def test_out_of_range_rejected(self, field, bad_value):
        with pytest.raises(ValidationError):
            RiskConfig.model_validate({field: bad_value})


class TestD9LeverageConsistency:
    """D9-3: risk / margin / leverage / min_stop 은 독립적이지 않다."""

    def test_default_is_consistent(self):
        cfg = RiskConfig()
        assert cfg.max_notional_multiple == D("1.0")  # 0.10 × 10
        # 0.01 / 1.0 - 0.0015 = 0.0085
        assert cfg.implied_min_stop_distance == D("0.0085")
        assert cfg.min_stop_distance >= cfg.implied_min_stop_distance

    def test_too_tight_min_stop_is_rejected(self):
        """max_leverage=5 인데 min_stop 1% 면 캡이 상시 발동한다."""
        with pytest.raises(ValidationError, match="레버리지 상한이 함의하는"):
            RiskConfig.model_validate({"max_leverage": 5, "min_stop_distance": 0.01})

    def test_all_problems_reported_at_once(self):
        """첫 번째 문제에서 멈추면 사용자가 설정을 여러 번 고쳐야 한다."""
        with pytest.raises(ValidationError) as exc:
            RiskConfig.model_validate(
                {
                    "max_leverage": 5,
                    "min_stop_distance": 0.01,  # D9 위반
                    "daily_loss_limit_pct": 0.005,  # risk_per_trade(0.01) 미만
                }
            )
        message = str(exc.value)
        assert "[1]" in message and "[2]" in message
        assert "레버리지 상한이 함의하는" in message
        assert "단 한 번의 손절" in message

    def test_error_message_suggests_fix(self):
        with pytest.raises(ValidationError) as exc:
            RiskConfig.model_validate({"max_leverage": 5, "min_stop_distance": 0.01})
        message = str(exc.value)
        assert "max_leverage" in message
        assert "min_stop_distance" in message
        assert "D9-3" in message

    @pytest.mark.parametrize(
        ("max_leverage", "min_stop"),
        [
            (3, 0.035),  # live.yaml 조합
            (5, 0.02),
            (10, 0.01),  # base.yaml 조합
        ],
    )
    def test_documented_combinations_are_valid(self, max_leverage, min_stop):
        """05 §3.6 의 조합 표가 실제로 통과하는지 확인한다."""
        cfg = RiskConfig.model_validate(
            {"max_leverage": max_leverage, "min_stop_distance": min_stop}
        )
        assert cfg.min_stop_distance >= cfg.implied_min_stop_distance

    def test_widening_leverage_relaxes_constraint(self):
        """레버리지를 올리면 더 좁은 손절이 허용된다."""
        loose = RiskConfig.model_validate({"max_leverage": 10, "min_stop_distance": 0.01})
        tight = RiskConfig.model_validate({"max_leverage": 3, "min_stop_distance": 0.035})
        assert loose.implied_min_stop_distance < tight.implied_min_stop_distance


class TestLossLimitConsistency:
    def test_daily_limit_below_per_trade_risk_rejected(self):
        """한 번 손절하면 바로 하루 한도에 닿는 설정은 거부한다."""
        with pytest.raises(ValidationError, match="단 한 번의 손절"):
            # min_stop 도 함께 맞춰 D9 검증과 분리한다
            RiskConfig.model_validate(
                {
                    "risk_per_trade": 0.02,
                    "daily_loss_limit_pct": 0.01,
                    "min_stop_distance": 0.02,
                }
            )

    def test_drawdown_below_daily_limit_rejected(self):
        with pytest.raises(ValidationError, match="곧바로 HALTED"):
            RiskConfig.model_validate({"daily_loss_limit_pct": 0.05, "max_drawdown_pct": 0.05})

    def test_valid_ladder(self):
        cfg = RiskConfig.model_validate(
            {"risk_per_trade": 0.01, "daily_loss_limit_pct": 0.02, "max_drawdown_pct": 0.10}
        )
        assert cfg.risk_per_trade < cfg.daily_loss_limit_pct < cfg.max_drawdown_pct


class TestStopEconomics:
    def test_absurdly_tight_min_stop_rejected(self):
        """비용이 위험 예산의 대부분을 먹는 설정은 거부한다.

        D9 정합성은 통과하되 비용 검사만 걸리는 조합을 쓴다.
        """
        with pytest.raises(ValidationError, match="거래비용이"):
            RiskConfig.model_validate(
                {
                    "margin_allocation": 0.40,
                    "max_leverage": 10,
                    "min_stop_distance": 0.0025,
                }
            )

    def test_one_percent_stop_is_acceptable(self):
        cfg = RiskConfig()
        share = cfg.cost.fixed_cost_rate / (cfg.min_stop_distance + cfg.cost.fixed_cost_rate)
        assert share < D("0.35")
        assert share > D("0.10")  # 13% 수준 — 싸지는 않다


class TestVolatilityGate:
    def test_inverted_bounds_rejected(self):
        with pytest.raises(ValidationError, match="모든 거래가 차단"):
            VolatilityGateConfig.model_validate({"atr_pct_lower": 0.9, "atr_pct_upper": 0.5})


class TestPolicyThresholds:
    def test_removed_time_and_exit_threshold_settings_are_rejected(self):
        with pytest.raises(ValidationError):
            RiskConfig.model_validate({"max_holding_hours": 48})
        with pytest.raises(ValidationError):
            PolicyConfig.model_validate({"long_exit_threshold": 45.0})

    def test_entry_must_exceed_fifty(self):
        """long_score 50 은 중립이다. 그 이하를 진입선으로 두면 항상 진입한다."""
        with pytest.raises(ValidationError):
            PolicyConfig.model_validate({"long_entry_threshold": 40.0})


class TestEdgeTable:
    def test_resolve_requires_every_horizon_and_uses_worst_edge(self):
        table = EdgeTableConfig.model_validate(edge_table_payload())
        assert table.resolve(
            side=PositionSide.LONG,
            score=75.0,
            regime="normal",
        ) == (D("22"), 40)

    def test_missing_horizon_fails_closed(self):
        table = EdgeTableConfig.model_validate(edge_table_payload(missing_horizon=144))
        assert table.resolve(
            side=PositionSide.LONG,
            score=75.0,
            regime="normal",
        ) == (None, None)

    def test_tampered_artifact_is_rejected(self):
        payload = edge_table_payload()
        payload["observations"] = 999
        with pytest.raises(ValidationError, match="digest"):
            EdgeTableConfig.model_validate(payload)

    def test_table_contents_change_root_config_hash(self):
        from auto_trading.config.loader import compute_config_hash

        without = root()
        with_table = root(edge_table=edge_table_payload())
        assert compute_config_hash(without) != compute_config_hash(with_table)


class TestUiGuardrails:
    def test_external_host_rejected(self):
        with pytest.raises(ValidationError, match="로컬 주소"):
            UiConfig.model_validate({"host": "0.0.0.0"})

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_local_hosts_allowed(self, host):
        assert UiConfig.model_validate({"host": host}).host == host

    def test_default_is_disabled_and_local(self):
        cfg = UiConfig()
        assert cfg.enabled is False
        assert cfg.host == "127.0.0.1"


class TestRootConfig:
    def test_minimal_valid_config(self):
        cfg = root()
        assert cfg.mode is Mode.BACKTEST
        assert cfg.symbol == "BTCUSDT"

    def test_missing_feature_timeframe_rejected(self):
        with pytest.raises(ValidationError, match="타임프레임"):
            root(data={"timeframes": ["1m", "5m"]})  # 4h, 15m, 1h 누락

    def test_one_minute_timeframe_required(self):
        with pytest.raises(ValidationError, match="1m 은 필수"):
            root(data={"timeframes": ["5m", "15m", "1h", "4h"]})

    def test_live_mode_rejects_high_leverage(self):
        with pytest.raises(ValidationError, match="live 모드에서 max_leverage"):
            root(mode="live", risk={"max_leverage": 10, "min_stop_distance": 0.01})

    def test_live_mode_rejects_flatten_killswitch(self):
        with pytest.raises(ValidationError, match="flatten"):
            root(
                mode="live",
                risk={
                    "max_leverage": 3,
                    "min_stop_distance": 0.035,
                    "killswitch": {"mode": "flatten"},
                },
            )

    def test_live_mode_accepts_phase8_settings(self):
        cfg = root(mode="live", risk={"max_leverage": 3, "min_stop_distance": 0.035})
        assert cfg.mode is Mode.LIVE
        assert cfg.risk.max_leverage == 3

    def test_backtest_mode_allows_higher_leverage(self):
        """live 제약은 live 에만 적용된다."""
        cfg = root(mode="backtest", risk={"max_leverage": 10, "min_stop_distance": 0.01})
        assert cfg.risk.max_leverage == 10

    def test_enabled_features(self):
        cfg = root()
        assert len(cfg.features.enabled_names) == 5

    def test_disabling_a_feature(self):
        features = {**FEATURES, "t3_volume": {"timeframe": "5m", "enabled": False}}
        cfg = RootConfig.model_validate({"features": features})
        assert "t3_volume" not in cfg.features.enabled_names
        assert len(cfg.features.enabled_names) == 4

    def test_summary_lines_are_human_readable(self):
        lines = root().summary_lines()
        joined = "\n".join(lines)
        assert "1.00%" in joined  # risk_per_trade
        assert "10x" in joined  # max_leverage
        assert len(lines) >= 4


class TestStopTimeframeRequirement:
    def test_stop_atr_timeframe_must_be_collected(self):
        with pytest.raises(ValidationError, match="타임프레임"):
            root(
                data={"timeframes": ["1m", "5m", "15m", "1h", "4h"]},
                risk={"stop": {"atr_timeframe": "1d"}},
            )

    def test_default_stop_timeframe_is_collected(self):
        cfg = root()
        assert cfg.risk.stop.atr_timeframe is Timeframe.H1
        assert Timeframe.H1 in cfg.data.timeframes
