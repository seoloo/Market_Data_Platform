"""D9 PositionSizingEngine 단위 테스트."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from auto_trading.config.schema import RiskConfig
from auto_trading.core.enums import PositionSide, VetoReason
from auto_trading.risk.sizing import PositionSizingEngine, SizingRequest

D = Decimal


def request(**overrides: object) -> SizingRequest:
    values: dict[str, object] = {
        "side": PositionSide.LONG,
        "equity": D("1000"),
        "entry_price": D("100000"),
        "stop_price": D("98000"),
        "stop_source": "swing_low",
        "qty_step": D("0.00001"),
        "price_tick": D("0.1"),
        "min_qty": D("0.00001"),
        "min_notional": D("5"),
        "maintenance_margin_rate": D("0.004"),
    }
    values.update(overrides)
    return SizingRequest(**values)  # type: ignore[arg-type]


class TestStopSelection:
    def test_long_wider_uses_confirmed_swing(self):
        selected = PositionSizingEngine(RiskConfig()).select_stop(
            side=PositionSide.LONG,
            entry_price=D("100"),
            atr=D("2"),
            price_tick=D("0.1"),
            confirmed_swing_price=D("96"),
        )
        assert selected.price == D("95.6")
        assert selected.source == "swing_low"

    def test_short_wider_uses_confirmed_swing(self):
        selected = PositionSizingEngine(RiskConfig()).select_stop(
            side=PositionSide.SHORT,
            entry_price=D("100"),
            atr=D("2"),
            price_tick=D("0.1"),
            confirmed_swing_price=D("104"),
        )
        assert selected.price == D("104.4")
        assert selected.source == "swing_high"

    def test_missing_swing_falls_back_to_atr(self):
        selected = PositionSizingEngine(RiskConfig()).select_stop(
            side=PositionSide.LONG,
            entry_price=D("100"),
            atr=D("2"),
            price_tick=D("0.1"),
        )
        assert selected == (selected.__class__)(price=D("97.0"), source="atr")

    def test_flat_side_rejected(self):
        with pytest.raises(ValueError, match="FLAT"):
            PositionSizingEngine(RiskConfig()).select_stop(
                side=PositionSide.FLAT,
                entry_price=D("100"),
                atr=D("2"),
                price_tick=D("0.1"),
            )


class TestClosedFormSizing:
    def test_two_percent_reference_case(self):
        result = PositionSizingEngine(RiskConfig()).calculate(request())
        assert result.verdict.approved
        assert result.plan is not None
        plan = result.plan
        assert D("464") <= plan.target_notional < D("465")
        assert plan.actual_notional == D("464.00000")
        assert plan.exchange_leverage == 5
        assert plan.margin_allocated == D("92.80000")
        assert plan.estimated_loss_at_stop <= D("10")
        assert plan.stop_distance_pct == D("0.02")

    def test_ten_percent_stop_reproduces_d4_special_case(self):
        result = PositionSizingEngine(RiskConfig()).calculate(request(stop_price=D("90000")))
        assert result.plan is not None
        assert D("98") <= result.plan.target_notional < D("99")
        assert result.plan.exchange_leverage == 1

    @pytest.mark.parametrize("stop_price", ["99000", "98000", "95000", "90000"])
    def test_estimated_loss_never_exceeds_budget(self, stop_price: str):
        result = PositionSizingEngine(RiskConfig()).calculate(request(stop_price=D(stop_price)))
        assert result.plan is not None
        assert result.plan.estimated_loss_at_stop <= result.plan.max_loss

    def test_short_uses_positive_distance_and_higher_liquidation_price(self):
        result = PositionSizingEngine(RiskConfig()).calculate(
            request(side=PositionSide.SHORT, stop_price=D("102000"), stop_source="swing_high")
        )
        assert result.plan is not None
        assert result.plan.stop_distance_pct == D("0.02")
        assert result.plan.liquidation_price > result.plan.entry_price

    def test_expected_adverse_funding_reduces_notional(self):
        engine = PositionSizingEngine(RiskConfig())
        without = engine.calculate(request(expected_funding_rate=D("0")))
        with_funding = engine.calculate(request(expected_funding_rate=D("0.001")))
        assert without.plan is not None and with_funding.plan is not None
        assert with_funding.plan.target_notional < without.plan.target_notional

    def test_plan_is_immutable(self):
        result = PositionSizingEngine(RiskConfig()).calculate(request())
        assert result.plan is not None
        with pytest.raises((FrozenInstanceError, AttributeError)):
            result.plan.qty = D("1")  # type: ignore[misc]


class TestSizingVetoes:
    def test_invalid_long_stop_veto(self):
        result = PositionSizingEngine(RiskConfig()).calculate(request(stop_price=D("100100")))
        assert result.verdict.reasons == (VetoReason.INVALID_STOP,)
        assert result.plan is None

    def test_invalid_short_stop_veto(self):
        result = PositionSizingEngine(RiskConfig()).calculate(
            request(side=PositionSide.SHORT, stop_price=D("99900"))
        )
        assert result.verdict.reasons == (VetoReason.INVALID_STOP,)

    def test_tight_stop_is_widened_by_default(self):
        result = PositionSizingEngine(RiskConfig()).calculate(request(stop_price=D("99500")))
        assert result.plan is not None
        assert result.plan.stop_widened
        assert result.plan.stop_price == D("99000.0")
        assert result.plan.stop_distance_pct == D("0.01")

    def test_tight_stop_skip_mode_veto(self):
        cfg = RiskConfig.model_validate({"tight_stop_action": "SKIP"})
        result = PositionSizingEngine(cfg).calculate(request(stop_price=D("99500")))
        assert result.verdict.reasons == (VetoReason.STOP_TOO_TIGHT,)

    def test_leverage_cap_reduces_position(self):
        # 정상 로딩에서는 D9 정합성 검증이 이 조합을 차단한다. 런타임 설정 드리프트를
        # 장애 주입해 엔진의 2차 방어가 안전 방향으로 동작하는지 확인한다.
        cfg = RiskConfig().model_copy(update={"max_leverage": 2, "min_stop_distance": D("0.04")})
        result = PositionSizingEngine(cfg).calculate(request(stop_price=D("96000")))
        assert result.plan is not None
        assert result.plan.leverage_capped
        assert result.plan.actual_notional <= D("200")
        assert result.plan.exchange_leverage == 2

    def test_leverage_cap_skip_mode_veto(self):
        cfg = RiskConfig.model_validate({"leverage_limit_action": "SKIP_TRADE"}).model_copy(
            update={"max_leverage": 2, "min_stop_distance": D("0.04")}
        )
        result = PositionSizingEngine(cfg).calculate(request(stop_price=D("96000")))
        assert result.verdict.reasons == (VetoReason.MAX_LEVERAGE,)

    def test_below_exchange_minimum_veto(self):
        result = PositionSizingEngine(RiskConfig()).calculate(request(min_notional=D("500")))
        assert result.verdict.reasons == (VetoReason.BELOW_MIN_SIZE,)

    def test_large_rounding_error_veto(self):
        result = PositionSizingEngine(RiskConfig()).calculate(request(qty_step=D("0.001")))
        assert result.verdict.reasons == (VetoReason.SIZING_RESOLUTION,)

    def test_liquidation_too_close_veto(self):
        result = PositionSizingEngine(RiskConfig()).calculate(
            request(maintenance_margin_rate=D("0.19"))
        )
        assert result.verdict.reasons == (VetoReason.LIQUIDATION_TOO_CLOSE,)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"equity": D("0")},
            {"entry_price": D("0")},
            {"qty_step": D("0")},
            {"price_tick": D("0")},
            {"maintenance_margin_rate": D("1")},
        ],
    )
    def test_invalid_exchange_inputs_raise(self, overrides: dict[str, Decimal]):
        with pytest.raises(ValueError):
            PositionSizingEngine(RiskConfig()).calculate(request(**overrides))


class TestRegimeRiskScale:
    """D14-2 — 국면 계수는 **크기**로만 들어온다."""

    def test_half_risk_scale_halves_the_position(self):
        engine = PositionSizingEngine(RiskConfig())
        full = engine.calculate(request()).plan
        half = engine.calculate(request(risk_scale=D("0.5"))).plan
        assert full is not None
        assert half is not None
        # 명목가는 허용 손실에 선형이므로 절반이 된다 (수량 내림 오차 범위 내).
        assert half.target_notional == pytest.approx(full.target_notional / 2, rel=Decimal("1e-9"))
        assert half.max_loss == full.max_loss / 2

    def test_effective_risk_is_recorded_not_the_nominal_one(self):
        """기록에 남는 risk_per_trade 는 **실제 적용된** 값이어야 재현이 된다."""
        plan = PositionSizingEngine(RiskConfig()).calculate(request(risk_scale=D("0.5"))).plan
        assert plan is not None
        assert plan.risk_per_trade == RiskConfig().risk_per_trade * D("0.5")

    def test_scaled_position_still_respects_the_loss_cap(self):
        plan = PositionSizingEngine(RiskConfig()).calculate(request(risk_scale=D("0.25"))).plan
        assert plan is not None
        assert plan.estimated_loss_at_stop <= plan.max_loss

    def test_default_scale_changes_nothing(self):
        engine = PositionSizingEngine(RiskConfig())
        assert engine.calculate(request()).plan == engine.calculate(request(risk_scale=D(1))).plan

    @pytest.mark.parametrize("bad", [D(0), D("-0.5"), D("1.5")])
    def test_scale_outside_unit_interval_is_rejected(self, bad):
        with pytest.raises(ValueError, match="risk_scale"):
            request(risk_scale=bad)
