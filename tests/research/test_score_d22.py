"""문서 207 — 종합 점수 특징의 미래 무관성 · 캔들 정의 · 포지션 · 체결 시점 · 릿지."""

from __future__ import annotations

import math

import pytest

from auto_trading.research.score_d22 import (
    FEATURES,
    Bars,
    _candles,
    features,
    fit_ridge,
    fit_univariate,
    forward_returns,
    positions_from,
    scores,
    simulate,
    stats,
)


def _bars(n: int, seed: float = 0.0) -> Bars:
    c = [
        100 * math.exp(0.2 * math.sin(k / 9 + seed) + 0.1 * math.sin(k / 3.7) + 0.0005 * k)
        for k in range(n)
    ]
    o = [c[0], *c[:-1]]
    h = [
        max(a, z) * (1 + 0.004 * (1 + math.sin(k)))
        for k, (a, z) in enumerate(zip(o, c, strict=True))
    ]
    lo = [
        min(a, z) * (1 - 0.004 * (1 + math.cos(k)))
        for k, (a, z) in enumerate(zip(o, c, strict=True))
    ]
    return Bars(list(range(n)), o, h, lo, c)


@pytest.mark.leakage
def test_features_do_not_use_future_bars() -> None:
    b = _bars(420)
    base = features(b)
    t = 330
    # t 뒤 봉을 크게 바꾼다
    k = t + 1
    b2 = Bars(
        b.t,
        [*b.o[:k], *(x * 1.7 for x in b.o[k:])],
        [*b.h[:k], *(x * 1.9 for x in b.h[k:])],
        [*b.low[:k], *(x * 0.6 for x in b.low[k:])],
        [*b.c[:k], *(x * 0.7 for x in b.c[k:])],
    )
    moved = features(b2)
    for f in FEATURES:
        assert moved[f][: t + 1] == base[f][: t + 1], f


def test_all_feature_groups_produce_values() -> None:
    f = features(_bars(600))
    elliott = [n for n in FEATURES if n.startswith("ew_")]
    for name in FEATURES:
        if name not in elliott:
            assert any(v not in (None, 0.0) for v in f[name]), name
    assert any(v not in (None, 0.0) for n in elliott for v in f[n])


def test_candle_definitions() -> None:
    # 하락 5 봉 뒤 상승 장악형
    o = [10.0, 9.8, 9.6, 9.4, 9.2, 9.0, 8.6]
    c = [9.8, 9.6, 9.4, 9.2, 9.0, 8.7, 9.3]
    h = [max(a, z) + 0.01 for a, z in zip(o, c, strict=True)]
    lo = [min(a, z) - 0.01 for a, z in zip(o, c, strict=True)]
    out = _candles(Bars(list(range(7)), o, h, lo, c))
    assert out["engulf"][6] == 1.0
    assert out["marubozu"][6] == 1.0
    # 망치형: 하락 뒤 아래 꼬리 길고 위 꼬리 없음
    o2, c2 = [*o[:6], 8.80], [*c[:6], 8.85]
    h2 = [*h[:6], 8.86]
    lo2 = [*lo[:6], 8.40]
    out2 = _candles(Bars(list(range(7)), o2, h2, lo2, c2))
    assert out2["hammer_star"][6] == 1.0


def test_positions_and_execution_delay() -> None:
    score: list[float | None] = [None, 2.0, -2.0, 0.1]
    assert positions_from(score, -1.0, 1.0, "both") == [0.0, 1.0, -1.0, 0.0]
    assert positions_from(score, -1.0, 1.0, "long") == [0.0, 1.0, 0.0, 0.0]
    b = Bars([0, 1, 2, 3, 4], [100.0, 100.0, 110.0, 99.0, 99.0], [1.0] * 5, [1.0] * 5, [1.0] * 5)
    sim = simulate(b, [1.0, -1.0, 0.0, 0.0, 0.0], [0, 1], 0.001)
    # t=0 롱 → 시가 1 → 시가 2 (+10%), 비용 0.1%; t=1 숏 → 시가 2 → 3 (−10%↓ = +10%), 비용 0.2%
    assert sim.returns[0] == pytest.approx(0.10 - 0.001)
    assert sim.returns[1] == pytest.approx(-(99 / 110 - 1) - 0.002)
    assert sim.trades == 2


def test_forward_returns_start_next_open() -> None:
    b = Bars([0, 1, 2, 3], [10.0, 11.0, 12.0, 13.0], [1.0] * 4, [1.0] * 4, [1.0] * 4)
    fr = forward_returns(b, 2)
    assert fr[0] == pytest.approx(13 / 11 - 1)
    assert fr[1] is None


def test_ridge_and_univariate_recover_sign() -> None:
    n = 300
    x1 = [math.sin(k) for k in range(n)]
    x2 = [math.cos(k / 3) for k in range(n)]
    feats: dict[str, list[float | None]] = {"a": list(x1), "b": list(x2)}
    y: list[float | None] = [2 * a - 0.5 * z for a, z in zip(x1, x2, strict=True)]
    idx = list(range(n))
    m = fit_ridge(feats, ["a", "b"], y, idx, 1e-6)
    assert m.weights[0] > 0 > m.weights[1]
    u = fit_univariate(feats, ["a", "b"], y, idx)
    assert u.weights[0] > 0 > u.weights[1]
    sc = scores(feats, m, n)
    assert all(s is not None for s in sc)


def test_stats_basic() -> None:
    s = stats([0.1, -0.5, 0.2], 3)
    assert s["mdd"] == pytest.approx(0.5)
