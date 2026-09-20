"""money 모듈 테스트.

수량 정밀도 오류는 주문 거부와 dust 포지션의 원인이므로 (09-failure-modes.md F10)
경계값을 촘촘히 검증한다.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import PrecisionError
from auto_trading.core.money import (
    ZERO,
    add_bps,
    bps_between,
    ceil_to_step,
    dec,
    floor_to_step,
    is_dust,
    round_to_tick,
    safe_div,
)

D = Decimal


class TestDec:
    def test_float_is_rejected(self):
        """float 은 런타임에 거부한다. 규칙을 코드로 강제하는 것이 핵심이다."""
        with pytest.raises(PrecisionError, match="float"):
            dec(0.1)  # type: ignore[arg-type]

    def test_float_zero_is_also_rejected(self):
        with pytest.raises(PrecisionError):
            dec(0.0)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0.1", D("0.1")),
            ("100", D("100")),
            (42, D("42")),
            ("-1.5", D("-1.5")),
            ("1E+2", D("100")),
        ],
    )
    def test_accepts_str_and_int(self, value, expected):
        assert dec(value) == expected

    def test_preserves_exact_decimal_representation(self):
        """문자열 경로는 float 오차를 만들지 않는다."""
        assert str(dec("0.1")) == "0.1"
        assert dec("0.1") + dec("0.2") == dec("0.3")

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
    def test_rejects_non_finite(self, bad):
        with pytest.raises(PrecisionError):
            dec(bad)

    def test_rejects_garbage(self):
        with pytest.raises(PrecisionError):
            dec("abc")


class TestFloorToStep:
    @pytest.mark.parametrize(
        ("value", "step", "expected"),
        [
            ("0.0019", "0.001", "0.001"),
            ("0.0010", "0.001", "0.001"),
            ("0.00099", "0.001", "0"),
            ("1.23456", "0.001", "1.234"),
            ("100", "0.001", "100"),
            ("0", "0.001", "0"),
        ],
    )
    def test_always_rounds_down(self, value, step, expected):
        assert floor_to_step(D(value), D(step)) == D(expected)

    def test_never_exceeds_input(self):
        """올림하면 증거금 초과 주문이 나간다. 절대 입력보다 커지면 안 된다."""
        for raw in ("0.0019", "1.9999", "0.5", "123.456789"):
            result = floor_to_step(D(raw), D("0.001"))
            assert result <= D(raw)

    @given(
        value=st.decimals(min_value=D("0"), max_value=D("1000"), places=8),
        step_exp=st.integers(min_value=1, max_value=6),
    )
    def test_result_is_always_multiple_of_step(self, value, step_exp):
        step = D(1).scaleb(-step_exp)
        result = floor_to_step(value, step)
        assert result <= value
        assert (result / step) % 1 == 0

    def test_rejects_non_positive_step(self):
        for bad_step in ("0", "-0.001"):
            with pytest.raises(PrecisionError):
                floor_to_step(D("1"), D(bad_step))


class TestCeilToStep:
    def test_rounds_up(self):
        assert ceil_to_step(D("0.0011"), D("0.001")) == D("0.002")
        assert ceil_to_step(D("0.001"), D("0.001")) == D("0.001")


class TestRoundToTick:
    def test_half_up_by_default(self):
        assert round_to_tick(D("100.05"), D("0.1")) == D("100.1")
        assert round_to_tick(D("100.04"), D("0.1")) == D("100.0")

    def test_explicit_rounding_for_conservative_stops(self):
        """롱 스톱은 내림, 숏 스톱은 올림이 보수적이다."""
        assert round_to_tick(D("90.07"), D("0.1"), rounding=ROUND_FLOOR) == D("90.0")
        assert round_to_tick(D("90.01"), D("0.1"), rounding=ROUND_CEILING) == D("90.1")


class TestIsDust:
    @pytest.mark.parametrize(
        ("qty", "expected"),
        [("0", True), ("0.0009", True), ("0.001", False), ("-0.0005", True), ("-0.002", False)],
    )
    def test_dust_threshold(self, qty, expected):
        assert is_dust(D(qty), D("0.001")) is expected


class TestSafeDiv:
    def test_zero_denominator_returns_default(self):
        """손절폭 0 같은 상황에서 사이징이 폭주하지 않아야 한다."""
        assert safe_div(D("100"), ZERO) == ZERO
        assert safe_div(D("100"), ZERO, default=D("-1")) == D("-1")

    def test_normal_division(self):
        assert safe_div(D("10"), D("4")) == D("2.5")


class TestBps:
    def test_add_bps(self):
        assert add_bps(D("100"), D("10")) == D("100.1")
        assert add_bps(D("100"), D("-10")) == D("99.9")

    def test_bps_between(self):
        assert bps_between(D("100"), D("100.1")) == D("10")
        assert bps_between(D("100"), D("99.9")) == D("-10")

    def test_bps_between_zero_reference(self):
        assert bps_between(ZERO, D("100")) == ZERO

    def test_round_trip(self):
        """13bps 왕복 비용 계산이 정확해야 한다."""
        entry = D("100000")
        after = add_bps(entry, D("13"))
        assert bps_between(entry, after) == D("13")
