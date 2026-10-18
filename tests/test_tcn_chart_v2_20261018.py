"""TCN V2 정답 · 정책 · 통계 · 실행 감사 — torch 없이 도는 계약 시험.

운영 .venv 에는 numpy 가 없어 배열 시험은 건너뛴다. 연구 venv 에서는 전부 돈다:
``build/tcn-venv`` + 임시 pytest (PYTHONPATH) 로 실행.
"""

import math

import pytest
from scripts.tcn_chart_20261018 import execution
from scripts.tcn_chart_v2_20261018 import exec_audit, policy, stats, target

H4 = target.H4


def bars(n: int) -> list[list[float]]:
    return [[i * H4, 100 + i, 102 + i, 99 + i, 100 + i * 1.5, 1000] for i in range(n)]


def test_log_return_is_close_to_close_24h() -> None:
    h = bars(20)
    assert target.log_return_24h(h, 3) == pytest.approx(math.log(h[9][4] / h[3][4]))
    assert math.isnan(target.log_return_24h(h, 14))  # 끝 — 6 봉 뒤 없음


def test_gap_makes_target_missing() -> None:
    h = bars(20)
    for r in h[7:]:
        r[0] += H4  # 봉 하나 빠짐
    assert math.isnan(target.log_return_24h(h, 3))
    assert not math.isnan(target.log_return_24h(h, 8))


@pytest.mark.leakage
def test_target_maps_decision_time_to_bar_end_and_ignores_later_bars() -> None:
    np = pytest.importorskip("numpy")
    h = bars(30)
    times = np.asarray([h[5][0] + H4, h[10][0] + H4])  # 결정 시각 = 봉 끝
    y = target.targets(times, np.asarray([0, 0]), [np.asarray(h)])
    assert y[0] == pytest.approx(math.log(h[11][4] / h[5][4]))
    longer = np.asarray([*bars(30), [30 * H4, 1, 1, 1, 1e9, 1]])  # 먼 미래 봉 추가
    assert target.targets(times, np.asarray([0, 0]), [longer])[0] == y[0]


def test_policy_threshold_is_strict_and_nan_is_flat() -> None:
    pytest.importorskip("numpy")
    d = policy.decide([0.0019, 0.00191, -0.0019, -0.002, 0.0, math.nan])
    assert d.tolist() == [0, 1, 0, 2, 0, 0]
    assert policy.decide([0.0001, -0.0001], 0.0).tolist() == [1, 2]


def test_selection_scores_direction_times_return() -> None:
    np = pytest.importorskip("numpy")
    pred = np.asarray([0.01, -0.01, 0.0, 0.01])
    y = np.asarray([0.02, 0.03, 0.5, math.nan])
    s = policy.selection(pred, y, np.ones(4, bool))
    assert s["signals"] == 2 and s["long"] == 1 and s["short"] == 1
    assert s["mean_directional_log_return"] == pytest.approx((0.02 - 0.03) / 2)
    assert s["hit_rate"] == 0.5


def test_regression_metrics() -> None:
    np = pytest.importorskip("numpy")
    y = np.asarray([0.01, -0.02, 0.03, math.nan])
    r = stats.regression(y, np.asarray([0.01, -0.02, 0.03, 9.0]), mean=0.0)
    assert r["n"] == 3 and r["mse"] == 0 and r["r2_vs_zero"] == 1 and r["direction_hit"] == 1
    z = stats.regression(y, np.zeros(4), mean=0.0)
    assert z["r2_vs_zero"] == 0 and z["direction_hit"] == 0


def test_newey_west() -> None:
    np = pytest.importorskip("numpy")
    d = np.asarray([1.0, -1.0] * 50)
    assert stats.nw_t(d, 0)["t"] == pytest.approx(0)
    rng = np.random.default_rng(0)
    e = rng.normal(0.5, 1, 400)
    plain = e.mean() / (e.std() / math.sqrt(len(e)))
    assert stats.nw_t(e, 0)["t"] == pytest.approx(plain)
    assert stats.nw_t(e)["t"] > 2


def test_loss_diff_averages_assets_per_time() -> None:
    np = pytest.importorskip("numpy")
    times = np.asarray([2, 1, 1, 2])
    y = np.asarray([1.0, 1.0, 1.0, 1.0])
    d = stats.loss_diff_by_time(times, y, np.zeros(4), np.asarray([1.0, 0.0, 1.0, 1.0]))
    assert d.tolist() == [0.5, 1.0]  # 시각 1: (0 + 1)/2, 시각 2: 1


def test_halves_split_by_unique_time() -> None:
    np = pytest.importorskip("numpy")
    a, b = stats.halves(np.asarray([1, 1, 2, 2, 3, 4]))
    assert a.tolist() == [True, True, True, True, False, False] and (a ^ b).all()


@pytest.mark.parametrize(
    "bar",
    [
        [60000, 100, 101, 99, 100, 10],  # 범위 넓음 → 체결
        [60000, 100, 100.01, 99.99, 100, 10],  # 좁음 → 범위 밖
        [60000, 102, 103, 101, 102, 10],  # ±0.3% 한도 밖
        [60000, 100, 101, 99, 100, 0],  # 거래량 0
    ],
)
def test_audit_reason_agrees_with_v1_limit_fill(bar: list[float]) -> None:
    previous = [0, 100, 101, 99, 100, 10]
    for side in (1, -1):
        fill = execution.limit_fill(previous, bar, side, "ETHUSDT", 1)
        why = exec_audit.reason(previous, bar, side, "ETHUSDT")
        assert (fill is not None) == (why == "FILLABLE"), (bar, side, why)
    assert exec_audit.reason(previous, [60000, 100, 100.01, 99.99, 100, 10], 1, "ETHUSDT") == (
        "OUT_OF_RANGE_MARKETABLE"
    )
