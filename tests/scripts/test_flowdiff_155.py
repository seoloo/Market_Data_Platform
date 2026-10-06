from __future__ import annotations

import pytest
from scripts.audit_mm_spread import spread_bp
from scripts.run_flowdiff_155 import NORM_BARS, _ms, clustered, flow_z, quintile_cuts, trades


def test_microsecond_spot_timestamps_become_ms() -> None:
    assert _ms("1785542400000000") == 1785542400000
    assert _ms("1785542400000") == 1785542400000


def test_flow_z_uses_only_past_volume() -> None:
    bars = dict.fromkeys(range(NORM_BARS), (1.0, 100.0, 50.0))
    bars[NORM_BARS] = (1.0, 1000.0, 1000.0)  # 오늘 거래대금은 분모에 안 들어감
    z = flow_z(bars)
    assert list(z) == [NORM_BARS]
    assert z[NORM_BARS] == pytest.approx((2 * 1000 - 1000) / 100.0)


def test_trades_long_top_short_bottom_with_cost() -> None:
    obs: list[dict[str, float | str]] = [
        {"day": "d1", "s": 5.0, "ret": 0.02},
        {"day": "d2", "s": -5.0, "ret": -0.01},
        {"day": "d3", "s": 0.0, "ret": 0.5},
    ]
    got = trades(obs, "s", (-1.0, 1.0), 1, 0.001)
    assert [d for d, _ in got] == ["d1", "d2"]
    assert [v for _, v in got] == pytest.approx([0.019, 0.009])
    flipped = trades(obs, "s", (-1.0, 1.0), -1, 0.0)
    assert [v for _, v in flipped] == pytest.approx([-0.02, -0.01])


def test_quintile_cuts_and_clustered_se() -> None:
    assert quintile_cuts([float(i) for i in range(11)]) == (2.0, 8.0)
    same_day = clustered([("a", 0.01), ("a", 0.01), ("b", -0.01), ("b", -0.01)])
    split = clustered([("a", 0.01), ("b", 0.01), ("c", -0.01), ("d", -0.01)])
    assert same_day["se"] > split["se"]


def test_spread_bp() -> None:
    assert spread_bp(99.99, 100.01) == pytest.approx(2.0)
    assert spread_bp(0.0, 1.0) is None
