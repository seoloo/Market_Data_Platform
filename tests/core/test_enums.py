"""enums 테스트 — 특히 봉 경계 계산. 여기가 틀리면 전략 전체가 틀린다."""

from __future__ import annotations

import pytest

from auto_trading.core.enums import (
    Action,
    Mode,
    OrderState,
    PositionSide,
    PositionState,
    Side,
    SystemState,
    Timeframe,
)


class TestTimeframe:
    @pytest.mark.parametrize(
        ("tf", "minutes"),
        [
            (Timeframe.M1, 1),
            (Timeframe.M5, 5),
            (Timeframe.M15, 15),
            (Timeframe.H1, 60),
            (Timeframe.H4, 240),
            (Timeframe.D1, 1440),
        ],
    )
    def test_durations(self, tf, minutes):
        assert tf.minutes == minutes
        assert tf.ms == minutes * 60_000

    def test_all_timeframes_have_duration(self):
        for tf in Timeframe:
            assert tf.ms > 0

    def test_open_ms_alignment(self):
        # 2024-01-01T00:00:00Z = 1704067200000
        base = 1_704_067_200_000
        assert Timeframe.H4.open_ms_of(base + 1) == base
        assert Timeframe.H4.open_ms_of(base + Timeframe.H4.ms - 1) == base
        assert Timeframe.H4.open_ms_of(base + Timeframe.H4.ms) == base + Timeframe.H4.ms

    def test_close_ms_is_last_millisecond(self):
        base = 1_704_067_200_000
        assert Timeframe.M1.close_ms_of(base) == base + 59_999

    def test_is_closed_at(self):
        """형성 중인 봉을 전략에 쓰지 않기 위한 판정."""
        base = 1_704_067_200_000
        assert Timeframe.M1.is_closed_at(base, base + 59_998) is False
        assert Timeframe.M1.is_closed_at(base, base + 59_999) is True
        assert Timeframe.M1.is_closed_at(base, base + 60_000) is True

    def test_all_timeframes_divide_epoch_evenly(self):
        """UTC epoch 기준 정렬이 성립해야 봉 경계가 일관된다."""
        day_ms = 86_400_000
        for tf in Timeframe:
            assert day_ms % tf.ms == 0


class TestSide:
    def test_opposite(self):
        assert Side.BUY.opposite is Side.SELL
        assert Side.SELL.opposite is Side.BUY

    def test_sign(self):
        assert Side.BUY.sign == 1
        assert Side.SELL.sign == -1


class TestPositionSide:
    def test_closing_side(self):
        assert PositionSide.LONG.closing_side is Side.SELL
        assert PositionSide.SHORT.closing_side is Side.BUY

    def test_flat_has_no_closing_side(self):
        with pytest.raises(ValueError, match="FLAT"):
            _ = PositionSide.FLAT.closing_side


class TestOrderState:
    def test_terminal_states(self):
        assert OrderState.FILLED.is_terminal
        assert OrderState.CANCELED.is_terminal
        assert not OrderState.NEW.is_terminal
        assert not OrderState.UNKNOWN.is_terminal

    def test_unknown_blocks_entry(self):
        """UNKNOWN 은 종료 상태가 아니며 신규 진입을 막는다."""
        assert OrderState.UNKNOWN.blocks_new_entry
        assert not OrderState.NEW.blocks_new_entry

    def test_active_states(self):
        assert OrderState.NEW.is_active
        assert OrderState.PARTIALLY_FILLED.is_active
        assert not OrderState.FILLED.is_active


class TestPositionState:
    def test_protective_order_required_only_when_settled(self):
        assert PositionState.OPEN.requires_protective_order
        assert PositionState.REDUCING.requires_protective_order
        # UNPROTECTED 는 정의상 아직 보호주문이 없는 과도 상태다
        assert not PositionState.UNPROTECTED.requires_protective_order
        assert not PositionState.FLAT.requires_protective_order

    def test_exposure(self):
        assert PositionState.UNPROTECTED.has_exposure
        assert PositionState.OPEN.has_exposure
        assert not PositionState.FLAT.has_exposure
        assert not PositionState.CLOSED.has_exposure


class TestSystemState:
    def test_only_running_allows_entry(self):
        assert SystemState.RUNNING.allows_new_entry
        assert not SystemState.DEGRADED.allows_new_entry
        assert not SystemState.HALTED.allows_new_entry


class TestAction:
    def test_safe_direction_bypasses_risk_gates(self):
        """청산은 어떤 상태에서도 막히면 안 된다 (AGENTS.md §3.6)."""
        assert Action.CLOSE.is_safe_direction
        assert Action.REDUCE.is_safe_direction
        assert Action.ADJUST_STOP.is_safe_direction
        assert not Action.OPEN_LONG.is_safe_direction
        assert not Action.OPEN_SHORT.is_safe_direction

    def test_entry_actions(self):
        assert Action.OPEN_LONG.is_entry
        assert Action.OPEN_SHORT.is_entry
        assert not Action.CLOSE.is_entry


class TestMode:
    def test_only_live_is_real_money(self):
        assert Mode.LIVE.is_real_money
        for mode in (Mode.BACKTEST, Mode.PAPER, Mode.TESTNET):
            assert not mode.is_real_money


def test_enum_values_are_stable():
    """값은 DB 에 저장되므로 바꾸면 과거 기록과 호환이 깨진다.

    이 테스트는 값 변경을 의도적으로 어렵게 만들기 위한 것이다.
    """
    assert Mode.BACKTEST.value == "backtest"
    assert Timeframe.H4.value == "4h"
    assert Side.BUY.value == "BUY"
    assert OrderState.PARTIALLY_FILLED.value == "PARTIALLY_FILLED"
    assert PositionState.UNPROTECTED.value == "UNPROTECTED"
