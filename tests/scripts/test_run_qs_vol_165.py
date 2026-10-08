from __future__ import annotations

import pytest
from scripts.run_qs_vol_165 import vol_managed

DAYS = [f"2020-01-{i:02d}" for i in range(1, 31)] + [f"2020-02-{i:02d}" for i in range(1, 29)]


def _path(rets: list[float]) -> dict[str, float]:
    out, p = {DAYS[0]: 100.0}, 100.0
    for d, r in zip(DAYS[1:], rets, strict=False):
        p *= 1 + r
        out[d] = p
    return out


def test_calm_market_stays_three_times() -> None:
    base = _path([0.001] * (len(DAYS) - 1))
    price, lev = vol_managed(DAYS, base, {}, 0.60)
    assert set(lev.values()) == {3.0}
    # 금리 0 · 보수만: 하루 3 × 0.1% − 0.84%/252
    assert price[DAYS[1]] == pytest.approx(100 * (1 + 0.003 - 0.0084 / 252))


def test_wild_market_drops_to_one_and_uses_only_past() -> None:
    rets = [0.04 if i % 2 else -0.04 for i in range(len(DAYS) - 1)]  # 연 약 63% 변동성
    base = _path(rets)
    _price, lev = vol_managed(DAYS, base, {}, 0.40)
    assert lev[DAYS[25]] == 1.0
    assert lev[DAYS[20]] == 3.0  # 20 일이 차기 전에는 3 배
    # 미래 수익을 바꿔도 그 전 배율은 같다
    calm_after = rets[:30] + [0.0] * (len(rets) - 30)
    _p2, lev2 = vol_managed(DAYS, _path(calm_after), {}, 0.40)
    assert [lev2[d] for d in DAYS[:31]] == [lev[d] for d in DAYS[:31]]


def test_band_ignores_small_changes() -> None:
    rets = [
        0.012 if i % 2 else -0.012 for i in range(len(DAYS) - 1)
    ]  # 연 약 19% → 목표 50% 면 2.6 배
    _price, lev = vol_managed(DAYS, _path(rets), {}, 0.50)
    assert set(lev.values()) == {3.0}  # 3 − 2.6 = 0.4 < 띠 0.5 → 그대로
    _price, lev = vol_managed(DAYS, _path(rets), {}, 0.40)
    assert lev[DAYS[-1]] == pytest.approx(0.40 / (0.012 * 252**0.5), rel=0.05)  # 2.1 배로 바꿈
