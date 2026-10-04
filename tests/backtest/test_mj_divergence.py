from __future__ import annotations

import pytest

from auto_trading.backtest.ema_box import Bars
from auto_trading.backtest.mj_divergence import (
    M0,
    M1,
    Signal,
    SignalConfig,
    boxes,
    confluent,
    pivots,
    signals,
    simulate,
)

H = 3_600_000


def bars(rows: list[tuple[float, float, float, float]]) -> Bars:
    n = len(rows)
    return Bars(
        [k * H for k in range(n)],
        [r[0] for r in rows],
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
        [1.0] * n,
    )


def flat(n: int, px: float = 100.0) -> list[tuple[float, float, float, float]]:
    return [(px, px + 0.5, px - 0.5, px)] * n


def test_pivot_needs_strictly_lower_than_left_and_not_above_right() -> None:
    rows = flat(10)
    rows[6] = (100.0, 100.5, 98.0, 100.0)
    lows, _ = pivots(bars(rows), SignalConfig())
    assert [k for k, v in enumerate(lows) if v] == [6]
    rows[8] = (100.0, 100.5, 97.0, 100.0)  # 뒤 봉이 더 낮으면 저점이 아니다
    lows, _ = pivots(bars(rows), SignalConfig())
    assert not lows[6]


def _divergence_series() -> list[tuple[float, float, float, float]]:
    """긴 하락(RSI 과매도) → 저점 q → 반등 → 더 낮은 저점 p(하락 폭 작음) → 반등."""
    closes: list[float] = [200.0 - 2.0 * k for k in range(40)]  # 200 → 122
    closes += [115.0, 112.0, 118.0, 121.0, 123.0, 124.0, 122.0, 120.0, 118.0, 116.0]
    closes += [111.0, 116.0, 119.0, 121.0]
    rows = []
    prev = closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + 0.2, min(prev, c) - 0.2, c))
        prev = c
    return rows


def test_bullish_divergence_found_and_confirmed_two_bars_later() -> None:
    b = bars(_divergence_series())
    sigs = [s for s in signals(b, SignalConfig()) if s.side > 0]
    assert sigs, "상승 다이버전스가 하나는 나와야 한다"
    s = sigs[-1]
    assert s.i == s.pivot + 2
    assert b.low[s.pivot] < b.low[s.prev]
    assert s.stop == b.low[s.pivot]


def _trade_bars(path: list[tuple[float, float, float, float]]) -> Bars:
    return bars([*flat(3), *path])


def _long_signal() -> Signal:
    # 봉 2 종가 100 에 지정가, 손절 98 → R = 2
    return Signal(i=2, side=1, pivot=0, prev=0, stop=98.0)


def test_m0_full_target_at_one_and_half_r() -> None:
    b = _trade_bars([(100.0, 101.0, 99.5, 100.5), (100.5, 103.2, 100.0, 103.0)])
    run = simulate(b, [_long_signal()], M0, from_ms=0, to_ms=10 * H, fee=0.0)
    (tr,) = run.trades
    assert tr.exit_kind == "target"
    assert tr.r == pytest.approx(1.5)


def test_stop_wins_when_both_hit_same_bar_and_gap_fills_at_open() -> None:
    b = _trade_bars([(100.0, 100.2, 99.5, 100.0), (99.0, 104.0, 97.0, 99.0)])
    (tr,) = simulate(b, [_long_signal()], M0, from_ms=0, to_ms=10 * H, fee=0.0).trades
    assert tr.exit_kind == "stop"
    assert tr.r == pytest.approx(-1.0)
    b = _trade_bars([(100.0, 100.2, 99.5, 100.0), (96.0, 96.5, 95.0, 96.0)])
    (tr,) = simulate(b, [_long_signal()], M0, from_ms=0, to_ms=10 * H, fee=0.0).trades
    assert tr.r == pytest.approx(-2.0)  # 갭 아래 시가 96 → (96 − 100) / 2


def test_m1_partial_then_breakeven_from_next_bar() -> None:
    path = [
        (100.0, 100.2, 99.5, 100.0),
        (100.0, 103.5, 99.0, 103.0),  # 1.5R(103) 50% 익절, 같은 봉 99 는 본전 손절 아님
        (103.0, 103.2, 99.9, 100.0),  # 다음 봉 본전(100) 손절
    ]
    (tr,) = simulate(
        _trade_bars(path), [_long_signal()], M1, from_ms=0, to_ms=10 * H, fee=0.0
    ).trades
    assert tr.exit_kind == "breakeven"
    assert tr.r == pytest.approx(0.5 * 1.5)


def test_m1_all_targets() -> None:
    path = [(100.0, 100.2, 99.5, 100.0), (100.0, 107.0, 99.8, 106.0), (106.0, 110.5, 105.0, 110.0)]
    (tr,) = simulate(
        _trade_bars(path), [_long_signal()], M1, from_ms=0, to_ms=10 * H, fee=0.0
    ).trades
    assert tr.exit_kind == "target"
    assert tr.r == pytest.approx(0.5 * 1.5 + 0.4 * 3 + 0.1 * 5)


def test_limit_entry_missed_and_short_side() -> None:
    b = _trade_bars([(101.0, 102.0, 100.5, 101.5)])
    run = simulate(b, [_long_signal()], M0, from_ms=0, to_ms=10 * H, fee=0.0)
    assert run.missed == 1 and not run.trades
    short = Signal(i=2, side=-1, pivot=0, prev=0, stop=102.0)
    b = _trade_bars([(100.0, 100.5, 99.0, 99.5), (99.5, 99.8, 96.5, 97.0)])
    (tr,) = simulate(b, [short], M0, from_ms=0, to_ms=10 * H, fee=0.0).trades
    assert tr.side == -1 and tr.r == pytest.approx(1.5)


def test_fee_and_narrow_stop_skip() -> None:
    b = _trade_bars([(100.0, 101.0, 99.5, 100.5), (100.5, 103.2, 100.0, 103.0)])
    (tr,) = simulate(b, [_long_signal()], M0, from_ms=0, to_ms=10 * H, fee=0.001).trades
    assert tr.r == pytest.approx(1.5 - (100 + 103) * 0.001 / 2)
    tight = Signal(i=2, side=1, pivot=0, prev=0, stop=99.9)
    run = simulate(b, [tight], M0, from_ms=0, to_ms=10 * H)
    assert run.skipped == 1 and not run.trades


def test_signal_while_holding_is_dropped() -> None:
    path = [(100.0, 100.2, 99.5, 100.0)] * 4
    sigs = [_long_signal(), Signal(i=4, side=1, pivot=0, prev=0, stop=98.0)]
    run = simulate(_trade_bars(path), sigs, M0, from_ms=0, to_ms=10 * H, fee=0.0)
    assert run.signals == 2 and run.busy == 1
    assert [t.exit_kind for t in run.trades] == ["end"]


def test_liquidation_comes_before_stop() -> None:
    b = _trade_bars([(100.0, 100.2, 99.5, 100.0), (100.0, 100.2, 98.3, 99.0)])
    run = simulate(b, [_long_signal()], M0, from_ms=0, to_ms=10 * H, fee=0.0, liq_mmr=0.005)
    (tr,) = run.trades
    assert tr.exit_kind == "liquidation"
    assert tr.r == pytest.approx(-1.0, abs=0.01)  # 청산가 98.5 + 청산 수수료 ≈ 증거금 전부
    plain = simulate(b, [_long_signal()], M0, from_ms=0, to_ms=10 * H, fee=0.0)
    assert plain.trades[0].exit_kind == "end"  # 손절(98)에는 안 닿았다


def test_box_confluence_window() -> None:
    rows = flat(40, 100.0)
    rows += [(100.0 - k, 100.5 - k, 99.0 - k, 99.0 - k) for k in range(1, 31)]
    b = bars(rows)
    cfg = SignalConfig(zone_from=5, zone_to=60)
    z = boxes(b, cfg)
    assert z.end and z.end[0] >= cfg.box_bars - 1
    inside = Signal(i=64, side=1, pivot=62, prev=50, stop=100.0)
    outside = Signal(i=64, side=1, pivot=62, prev=50, stop=80.0)
    assert confluent(inside, z, cfg)
    assert not confluent(outside, z, cfg)
