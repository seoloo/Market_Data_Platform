"""문서 86 — 여러 자산군 T/v2 시뮬레이터: 기존 T/v2 재현(V0), 쉬는 날, 비례 축소."""

from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from auto_trading.backtest.trend import DailyBar, TrendConfig, simulate_trend, target_weight
from auto_trading.backtest.xasset_trend import AssetSeries, simulate_multi, target_weight_bars

D = 86_400_000
T0 = 1_500_000_000_000 // D * D
CFG = replace(TrendConfig(), long_only=True)


def _walk(n: int, seed: int, drift: float = 0.001) -> list[float]:
    rng = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= math.exp(drift + rng.gauss(0, 0.03))
        out.append(price)
    return out


def test_crypto_only_matches_existing_t2_exactly() -> None:
    n = 500
    closes = {"BTC": _walk(n, 1), "ETH": _walk(n, 2, 0.0005)}
    bars = {
        s: {T0 + i * D: DailyBar(c * 1.01, c) for i, c in enumerate(v)} for s, v in closes.items()
    }
    lo, hi = T0 + 200 * D, T0 + (n - 1) * D
    a = simulate_trend(bars, {}, from_ms=lo, to_ms=hi, initial_equity=10_000.0, cfg=CFG)
    assets = {s: AssetSeries({d: b.close for d, b in v.items()}, 365) for s, v in bars.items()}
    b = simulate_multi(assets, from_ms=lo, to_ms=hi, initial_equity=10_000.0, cfg=CFG)
    assert b.equity == a.equity
    assert (b.trades, b.costs) == (a.trades, a.costs)


def test_target_weight_uses_bars_per_year() -> None:
    closes = _walk(200, 3)
    assert target_weight_bars(closes, CFG, 365) == target_weight(closes, CFG)
    w365, w252 = target_weight_bars(closes, CFG, 365), target_weight_bars(closes, CFG, 252)
    assert w365 is not None and w252 is not None
    if 0 < w365 < CFG.max_weight and 0 < w252 < CFG.max_weight:
        assert w252 / w365 == pytest.approx(math.sqrt(365 / 252))


def test_closed_days_have_no_pnl_and_no_trades() -> None:
    prices = _walk(400, 4, 0.004)
    weekdays = [T0 + i * D for i in range(400) if (i % 7) < 5]
    series = AssetSeries({d: prices[(d - T0) // D] for d in weekdays}, 252)
    lo = weekdays[200]
    r = simulate_multi({"SPY": series}, from_ms=lo, to_ms=T0 + 399 * D, initial_equity=1.0, cfg=CFG)
    open_days = set(r.trading_days["SPY"])
    for d, ret in zip(r.days, r.returns, strict=True):
        if d not in open_days:
            assert ret == 0.0
    assert set(r.trading_days["SPY"]) <= set(weekdays)


def test_targets_are_scaled_when_sum_exceeds_one() -> None:
    n = 400
    assets = {
        f"A{k}": AssetSeries({T0 + i * D: p for i, p in enumerate(_walk(n, 10 + k, 0.01))}, 365)
        for k in range(4)
    }
    r = simulate_multi(
        assets,
        from_ms=T0 + 200 * D,
        to_ms=T0 + (n - 1) * D,
        initial_equity=1.0,
        cfg=replace(CFG, target_vol=0.9),
    )
    assert r.target_sum_max == pytest.approx(1.0)
    assert r.gross_max <= 1.0 + CFG.band * 4 + 1e-9


def test_long_short_is_refused() -> None:
    with pytest.raises(ValueError):
        simulate_multi({}, from_ms=T0, to_ms=T0, initial_equity=1.0, cfg=TrendConfig())
