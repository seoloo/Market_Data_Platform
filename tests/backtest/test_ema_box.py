"""문서 139 — EMA 정배열 박스 돌파: 봉 묶기·지표·신호 조건·지정가 진입·크기·손절·분할 청산."""

from __future__ import annotations

from dataclasses import replace

import pytest

from auto_trading.backtest.ema_box import (
    Bars,
    BoxConfig,
    BoxResult,
    aggregate,
    atr,
    ema,
    hold_alignment,
    signal_at,
    simulate_box,
)
from auto_trading.backtest.ema_box import _indicators as indicators

D = 86_400_000
T0 = 1_514_764_800_000  # 2018-01-01
CFG = BoxConfig(warmup=0, fee=0.0)


def make(closes: list[float], *, vols: list[float] | None = None, spread: float = 0.2) -> Bars:
    n = len(closes)
    opens = [closes[0], *closes[:-1]]
    return Bars(
        [T0 + i * D for i in range(n)],
        opens,
        [max(o, c) + spread for o, c in zip(opens, closes, strict=True)],
        [min(o, c) - spread for o, c in zip(opens, closes, strict=True)],
        closes,
        vols or [100.0] * n,
    )


RAMP = [100 + 0.1 * i for i in range(80)]
BOX = [RAMP[-1] + d for d in (0.05, -0.05, 0.1, 0.0, 0.05, -0.05, 0.1, 0.0, 0.05, 0.1)]
N = len(RAMP) + len(BOX)  # 돌파 봉의 색인


def uptrend_with_box(breakout: float = RAMP[-1] + 1.0, vol: float = 500.0) -> Bars:
    """완만한 상승(EMA 가 따라붙음) → 10 봉 좁은 횡보 → 돌파 봉."""
    closes = [*RAMP, *BOX, breakout]
    return make(closes, vols=[100.0] * N + [vol])


def extend(b: Bars, tail: list[float]) -> Bars:
    return make([*b.c, *tail], vols=[*b.v, *([100.0] * len(tail))])


def test_aggregate_daily_bars() -> None:
    t = [T0, T0 + 60_000, T0 + D]
    b = aggregate(t, [1, 2, 3], [5, 6, 7], [0.5, 1.5, 2.5], [2, 3, 4], [1, 1, 1], D)
    assert list(b.t) == [T0, T0 + D]
    assert (b.o[0], b.h[0], b.low[0], b.c[0], b.v[0]) == (1, 6, 0.5, 3, 2)


def test_ema_and_atr_basics() -> None:
    assert ema([1, 1, 1], 10) == [1, 1, 1]
    e = ema([0, 10], 3)
    assert e[1] == pytest.approx(5.0)
    b = make([10.0] * 20, spread=1.0)
    a = atr(b, 14)
    assert a[12] is None and a[13] == pytest.approx(2.0)


def test_signal_requires_all_conditions() -> None:
    b = uptrend_with_box()
    ind = indicators(b, CFG)
    stop = signal_at(b, ind, N, CFG)
    assert stop == pytest.approx(min(b.low[N - 10 : N]))
    weak_vol = uptrend_with_box(vol=50.0)
    assert signal_at(weak_vol, indicators(weak_vol, CFG), N, CFG) is None  # S6
    no_break = uptrend_with_box(breakout=RAMP[-1] + 0.15)
    assert signal_at(no_break, indicators(no_break, CFG), N, CFG) is None  # S5
    down = make([200 - 0.1 * i for i in range(N + 1)])
    assert signal_at(down, indicators(down, CFG), N, CFG) is None  # S1


def run(b: Bars, cfg: BoxConfig = CFG) -> BoxResult:
    return simulate_box(b, from_ms=T0, to_ms=T0 + (len(b.c) - 1) * D, initial=10_000.0, cfg=cfg)


def test_limit_entry_fills_at_open_and_risks_one_percent() -> None:
    b = extend(uptrend_with_box(), [RAMP[-1] + 1.05])  # 다음 봉 시가 = 돌파 종가 ≤ 지정가
    r = run(b)
    assert (r.signals, r.entries, r.missed) == (1, 1, 0)


def test_limit_entry_missed_when_price_runs_away() -> None:
    b = uptrend_with_box()
    c = [*b.c, 115.0]
    o = [*b.o, 115.0]
    gap = Bars(
        [T0 + i * D for i in range(len(c))], o, [*b.h, 115.5], [*b.low, 114.0], c, [*b.v, 100.0]
    )
    r = run(gap)
    assert r.missed == 1 and r.entries == 0


def test_stop_fills_at_stop_and_is_minus_one_r() -> None:
    b = extend(uptrend_with_box(), [RAMP[-1] + 1.05, 100.0])
    r = run(b)
    assert r.stops == 1
    assert r.r_multiples[0] == pytest.approx(-1.0, abs=0.01)


def test_half_then_full_exit_on_consecutive_closes() -> None:
    top = RAMP[-1] + 1.0
    rise = [top + 0.4 * k for k in range(1, 16)]
    fall = [rise[-1] - 0.6 * k for k in range(1, 12)]
    r = run(extend(uptrend_with_box(), [*rise, *fall]))
    assert r.half_exits == 1 and r.full_exits == 1 and r.stops == 0
    assert r.r_multiples and r.r_multiples[0] > 0


def test_position_never_exceeds_equity() -> None:
    b = extend(uptrend_with_box(), [RAMP[-1] + 1.05, RAMP[-1] + 1.1])
    r = run(b, replace(CFG, risk=0.5))  # 위험을 크게 줘도 100% 상한
    assert max(r.exposure) <= 1.0 + 1e-9 and r.entries == 1


def test_alignment_hold_and_buy_hold() -> None:
    b = make([50 + i for i in range(80)])
    ah = hold_alignment(b, from_ms=T0, to_ms=T0 + 79 * D, initial=1000.0, cfg=CFG)
    bh = hold_alignment(b, from_ms=T0, to_ms=T0 + 79 * D, initial=1000.0, cfg=CFG, always=True)
    assert bh.equity[-1] == pytest.approx(1000 / 50 * 129)
    assert 1000.0 < ah.equity[-1] < bh.equity[-1]
