"""TCN V3 새 채널 · 실행기 E2 계약 시험 (torch 없음, 배열 시험은 numpy 있을 때만)."""

import math
from decimal import Decimal

import pytest
from scripts.tcn_chart_20261018 import execution
from scripts.tcn_chart_v3_20261018 import channels, exec_m

H4 = channels.H4


def test_taker_imbalance() -> None:
    np = pytest.importorskip("numpy")
    out = channels.taker_imbalance(np.asarray([3.0, 0.0, 1.0]), np.asarray([4.0, 2.0, 0.0]))
    assert out[0] == pytest.approx(0.25) and out[1] == pytest.approx(-0.5) and math.isnan(out[2])


@pytest.mark.leakage
def test_funding_last_uses_only_settled_rates() -> None:
    np = pytest.importorskip("numpy")
    f = np.asarray([[8 * 3600_000, 0.001], [16 * 3600_000 + 1, 0.002]])
    got = channels.funding_last(np.asarray([4, 8, 12, 16, 20]) * 3600_000, f)
    assert math.isnan(got[0])  # 첫 정산 전
    assert got[1:].tolist() == [0.001, 0.001, 0.001, 0.002]  # 16:00 정각엔 +1ms 정산 아직


@pytest.mark.leakage
def test_extra_window_ends_at_decision_bar() -> None:
    np = pytest.importorskip("numpy")
    n = 120
    h4 = np.asarray([[i * H4, 1, 1, 1, 1, 1] for i in range(n)], dtype=float)
    per = np.column_stack((np.arange(n, dtype=float), -np.arange(n, dtype=float)))
    times = np.asarray([100 * H4 + H4, 50 * H4])  # 봉 100 마감 · 창 부족
    out = channels.extra(times, np.asarray([0, 0]), [per], [h4])
    assert out[0, -1, 0] == 100 and out[0, 0, 0] == 5 and out.shape == (2, 96, 2)
    assert np.isnan(out[1]).all()


@pytest.mark.parametrize(
    "bar",
    [
        [60000, 100, 101, 99, 100, 10],
        [60000, 102, 103, 101, 102, 10],
        [60000, 100, 101, 99, 100, 0],
    ],
)
def test_e2_matches_v1_when_v1_fills_or_caps(bar: list[float]) -> None:
    previous = [0, 100, 101, 99, 100, 10]
    for side in (1, -1):
        v1 = execution.limit_fill(previous, bar, side, "ETHUSDT", 1)
        e2 = exec_m.marketable_fill(previous, bar, side, "ETHUSDT", 1)
        if v1 is not None:
            assert e2 == v1
        elif (side == 1 and bar[1] == 102) or bar[5] == 0:
            assert e2 is None  # 한도 · 거래량 0 은 그대로 미체결


def test_e2_fills_marketable_limit_outside_minute_range_at_full_adverse_price() -> None:
    previous = [0, 100, 101, 99, 100, 10]
    narrow = [60000, 100, 100.01, 99.99, 100, 10]
    assert execution.limit_fill(previous, narrow, 1, "ETHUSDT", 1) is None
    assert exec_m.marketable_fill(previous, narrow, 1, "ETHUSDT", 1) == Decimal("100.05")
    assert exec_m.marketable_fill(previous, narrow, -1, "ETHUSDT", 1) == Decimal("99.95")
    assert exec_m.marketable_fill(previous, narrow, 1, "ETHUSDT", 2) == Decimal("100.09")


def test_e2_context_swaps_and_restores_v1_fill() -> None:
    original = execution.limit_fill
    with exec_m.e2():
        assert execution.limit_fill is exec_m.marketable_fill
    assert execution.limit_fill is original
    with pytest.raises(RuntimeError), exec_m.e2():
        raise RuntimeError
    assert execution.limit_fill is original
