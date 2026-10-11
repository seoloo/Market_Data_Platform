"""문서 221 — funding 끄기 · 감싼 재생이 운영 엔진과 같은지 · 짝 부트스트랩."""

from __future__ import annotations

import math
from typing import Any

from auto_trading.backtest.overlay import O2_STOP, OverlayBar, simulate_overlay
from auto_trading.research.funding_gate_221 import (
    DAY_MS,
    block_bootstrap_sharpe_diff,
    funding_hot,
    sharpe,
    simulate_gated,
)

T0 = 1_600_000_000_000 // DAY_MS * DAY_MS


def _market(n: int = 260) -> tuple[Any, Any, Any]:
    spot: dict[str, dict[int, OverlayBar]] = {"BTCUSDT": {}, "ETHUSDT": {}}
    perp: dict[str, dict[int, OverlayBar]] = {"BTCUSDT": {}, "ETHUSDT": {}}
    for i in range(n):
        d = T0 + i * DAY_MS
        for s, base in (("BTCUSDT", 100.0), ("ETHUSDT", 50.0)):
            px = base * (1 + 0.004 * i + 0.03 * math.sin(i / 5))
            spot[s][d] = OverlayBar(px * 0.98, px)
            perp[s][d] = OverlayBar(px * 0.98, px * 1.001)
    funding = {
        s: [(T0 + i * DAY_MS // 3, 0.0005 if 200 <= i // 3 < 220 else 0.0001) for i in range(n * 3)]
        for s in spot
    }
    return spot, perp, funding


def test_funding_hot_uses_only_past_week() -> None:
    t = {"X": [T0 + k * DAY_MS // 3 for k in range(60)]}
    r = {"X": [0.0001] * 30 + [0.0005] * 30}
    day_mid = T0 + 10 * DAY_MS  # 직전 7 일 = 0.0001 만
    assert funding_hot(t, r, day_mid, 0.0003) == frozenset()
    day_late = T0 + 20 * DAY_MS  # 직전 7 일 = 0.0005
    assert funding_hot(t, r, day_late, 0.0003) == frozenset({"X"})
    assert funding_hot(t, r, T0, 0.0003) == frozenset()  # 정산 없음


def test_infinite_gate_matches_engine_exactly() -> None:
    spot, perp, funding = _market()
    kw: dict[str, Any] = {
        "from_ms": T0 + 130 * DAY_MS,
        "to_ms": T0 + 259 * DAY_MS,
        "initial_equity": 10_000.0,
    }
    ref = simulate_overlay(spot, perp, funding, cfg=O2_STOP, **kw)
    got, gated = simulate_gated(spot, perp, funding, cfg=O2_STOP, gate=math.inf, **kw)
    assert gated == 0
    assert got.equity == ref.equity
    assert got.funding == ref.funding


def test_gate_turns_off_futures_and_saves_funding() -> None:
    spot, perp, funding = _market()
    kw: dict[str, Any] = {
        "from_ms": T0 + 130 * DAY_MS,
        "to_ms": T0 + 259 * DAY_MS,
        "initial_equity": 10_000.0,
    }
    ref, _ = simulate_gated(spot, perp, funding, cfg=O2_STOP, gate=math.inf, **kw)
    got, gated = simulate_gated(spot, perp, funding, cfg=O2_STOP, gate=0.0003, **kw)
    assert gated > 0
    assert got.funding > ref.funding  # 덜 냄 (음수가 0 쪽으로)


def test_sharpe_and_bootstrap() -> None:
    a = [0.01, -0.005, 0.002, 0.004, -0.001] * 20
    assert sharpe(a) > 0
    p, lo, hi = block_bootstrap_sharpe_diff(a, a, block=5, reps=50)
    assert p == 0 and lo == 0 and hi == 0
