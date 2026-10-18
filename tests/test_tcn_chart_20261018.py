"""Production-environment compatible causal/contract tests (no optional ML imports)."""

import math
from decimal import Decimal

import pytest
from scripts.tcn_chart_20261018 import execution, features


def rows(n: int) -> list[list[float]]:
    return [[i * features.H4, 100 + i, 102 + i, 99 + i, 101 + i, 1000 + i] for i in range(n)]


@pytest.mark.leakage
def test_future_invariant() -> None:
    short = features.calculate(rows(120))
    long = features.calculate(rows(140))
    assert short[60:] == long[60:120]


def test_flat_wilder_and_price_scale() -> None:
    flat = [[i * features.H4, 100, 101, 99, 100, 1000] for i in range(160)]
    f = features.calculate(flat)[-1]
    assert f[3] == 0 and f[8] == 0
    assert f[9] == pytest.approx(0.02)
    source = rows(160)
    scaled = [[r[0], *[v * 10 for v in r[1:5]], r[5]] for r in source]
    assert features.calculate(source)[-1] == pytest.approx(features.calculate(scaled)[-1])


def test_gap_resets_warmup() -> None:
    a = rows(160)
    for r in a[100:]:
        r[0] += features.H4
    assert all(math.isnan(v) for v in features.calculate(a)[120])


@pytest.mark.parametrize(
    ("lo", "sh", "want"),
    [
        (-0.02, -0.01, 0),
        (0.003, -0.01, 1),
        (-0.01, 0.003, 2),
        (0.004, 0.004, 0),
        (None, 0.003, 2),
        (None, None, 0),
        (0.002, 0, 0),
    ],
)
def test_labels(lo: float | None, sh: float | None, want: int) -> None:
    assert features.classify(lo, sh) == want


@pytest.mark.leakage
def test_purge() -> None:
    assert features.split_id(1704067200000 - 26 * 3600000) == -1
    assert features.split_id(1704067200000 - 27 * 3600000) == 0
    assert features.split_id(1704067200000) == 1
    assert features.split_id(1788220800000) == -1


def test_bounded_fill_and_qty() -> None:
    previous = [0, 100, 101, 99, 100, 10]
    bar = [60000, 100, 101, 99, 100, 10]
    assert execution.limit_fill(previous, bar, 1, "ETHUSDT", 1) == Decimal("100.05")
    assert execution.limit_fill(previous, bar, -1, "ETHUSDT", 1) == Decimal("99.95")
    assert execution.limit_fill(previous, [60000, 102, 103, 101, 102, 10], 1, "ETHUSDT", 1) is None
    assert execution.limit_fill(previous, [60000, 100, 100, 100, 100, 10], 1, "ETHUSDT", 1) is None
    assert execution.floor_qty(Decimal("1.2349")) == Decimal("1.234")


def test_rule_not_reversed() -> None:
    assert features.rule([0] * 6 + [0.01, 0.02, 0.1, 0]) == 1
    assert features.rule([0] * 6 + [-0.01, -0.02, -0.1, 0]) == 2
    assert features.rule([0] * 10) == 0
