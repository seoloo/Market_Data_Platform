"""hashing 테스트 — 재현성의 기반이므로 결정론을 엄격히 검증한다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import Side
from auto_trading.core.hashing import canonical_json, sha256_hex


def test_dict_order_does_not_matter():
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})


def test_decimal_is_not_converted_to_float():
    """0.1 을 float 로 다루면 해시가 플랫폼/버전에 따라 달라질 수 있다."""
    assert sha256_hex(Decimal("0.1")) == sha256_hex(Decimal("0.1"))
    assert "0.1" in canonical_json(Decimal("0.1"))


def test_decimal_trailing_zeros_normalized():
    """1.10 과 1.1 은 같은 값이므로 같은 해시여야 한다."""
    assert sha256_hex(Decimal("1.10")) == sha256_hex(Decimal("1.1"))


def test_distinct_values_differ():
    assert sha256_hex(Decimal("1.1")) != sha256_hex(Decimal("1.2"))


def test_enum_uses_value():
    assert sha256_hex(Side.BUY) == sha256_hex("BUY")


def test_nested_structures():
    a = {"x": [1, {"y": Decimal("2.0")}], "z": None}
    b = {"z": None, "x": [1, {"y": Decimal("2")}]}
    assert sha256_hex(a) == sha256_hex(b)


def test_set_is_order_independent():
    assert sha256_hex({3, 1, 2}) == sha256_hex({1, 2, 3})


def test_rejects_unserializable():
    with pytest.raises(TypeError, match="직렬화할 수 없는"):
        sha256_hex(object())


def test_repeated_calls_are_stable():
    payload = {"symbol": "BTCUSDT", "price": Decimal("100000.5"), "tfs": ["1m", "4h"]}
    first = sha256_hex(payload)
    for _ in range(100):
        assert sha256_hex(payload) == first
