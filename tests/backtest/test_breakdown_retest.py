"""BRS (문서 205) — 미래 무관성 · 완성봉 · 직전 봉 창 · 신호 한 번 · 체결 · 비용 · 크기."""

from __future__ import annotations

import math

import pytest

from auto_trading.backtest.breakdown_retest import (
    DEFAULT,
    HOUR_MS,
    MIN15_MS,
    Breakdown,
    Config,
    breakdowns,
    daily_equity,
    drawdowns,
    max_streak,
    resample,
    simulate,
    summary,
)
from auto_trading.backtest.ema_box import Bars

T0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC


def _bars(closes: list[float], *, step: int = MIN15_MS, vol: float = 1.0) -> Bars:
    t = [T0 + k * step for k in range(len(closes))]
    o = [closes[0], *closes[:-1]]
    h = [max(a, b) * 1.001 for a, b in zip(o, closes, strict=True)]
    low = [min(a, b) * 0.999 for a, b in zip(o, closes, strict=True)]
    return Bars(t, o, h, low, list(closes), [vol] * len(closes))


def _flat(n: int, px: float = 100.0) -> Bars:
    t = [T0 + k * MIN15_MS for k in range(n)]
    return Bars(t, [px] * n, [px] * n, [px] * n, [px] * n, [1.0] * n)


# ------------------------------------------------------------------ 리샘플


def test_resample_keeps_only_complete_groups() -> None:
    b = _bars([100.0 + k for k in range(12)])
    t = list(b.t)
    keep = [k for k in range(12) if k != 5]  # 두 번째 1 시간 묶음에 빈 봉
    b2 = Bars(
        [t[k] for k in keep],
        [b.o[k] for k in keep],
        [b.h[k] for k in keep],
        [b.low[k] for k in keep],
        [b.c[k] for k in keep],
        [b.v[k] for k in keep],
    )
    h1 = resample(b2, 60)
    assert list(h1.t) == [T0, T0 + 2 * HOUR_MS]
    assert h1.o[0] == b.o[0] and h1.c[0] == b.c[3]
    assert h1.h[0] == max(b.h[:4]) and h1.low[0] == min(b.low[:4])
    assert h1.v[0] == 4.0


# ------------------------------------------------------------------ 붕괴 사건


def _h1_with_drop(n: int = 40, drop_at: int = 30) -> Bars:
    """완만한 하락 뒤 drop_at 봉에서 직전 20 봉 최저가 아래로 큰 거래량 마감."""
    closes = [200.0 - 0.1 * k for k in range(n)]
    closes[drop_at] = closes[drop_at - 1] - 3.0
    b = _bars(closes, step=HOUR_MS)
    v = list(b.v)
    v[drop_at] = 5.0
    return Bars(b.t, b.o, b.h, b.low, b.c, v)


def _h4_down(n: int = 260) -> Bars:
    return _bars([500.0 - k for k in range(n)], step=4 * HOUR_MS)


def _h4_aligned(h1: Bars) -> Bars:
    """1 시간봉보다 1000 시간 앞에서 시작해 끝까지 하락하는 4 시간봉."""
    start = h1.t[0] - 1000 * HOUR_MS
    n = (h1.t[-1] - start) // (4 * HOUR_MS) + 1
    closes = [900.0 - k for k in range(n)]
    b = _bars(closes, step=4 * HOUR_MS)
    shift = start - b.t[0]
    return Bars([t + shift for t in b.t], b.o, b.h, b.low, b.c, b.v)


def test_breakdown_found_with_level_from_prior_bars_only() -> None:
    h1 = _h1_with_drop()
    ev = breakdowns(h1, _h4_aligned(h1), Config(rsi_min=-1.0))
    k = 30
    hit = [e for e in ev if e.at_ms == h1.t[k] + HOUR_MS]
    assert hit
    assert hit[0].level == min(h1.low[k - 20 : k])  # 현재 봉 제외
    assert hit[0].level > h1.low[k]


@pytest.mark.leakage
def test_breakdowns_ignore_future_1h_and_unclosed_4h() -> None:
    h1 = _h1_with_drop()
    h4 = _h4_aligned(h1)
    cfg = Config(rsi_min=-1.0)
    base = breakdowns(h1, h4, cfg)
    k = 30
    at = h1.t[k] + HOUR_MS
    # k 뒤 1 시간봉을 크게 바꾼다
    c2 = list(h1.c[: k + 1]) + [x * 5 for x in h1.c[k + 1 :]]
    v2 = list(h1.v[: k + 1]) + [100.0] * (len(h1.v) - k - 1)
    h1b = Bars(h1.t, h1.o, h1.h, h1.low, c2, v2)
    # at 뒤에 마감하는 4 시간봉(진행 중 포함)을 상승으로 바꾼다
    c4 = [c if t + 4 * HOUR_MS <= at else 10_000.0 for t, c in zip(h4.t, h4.c, strict=True)]
    h4b = Bars(h4.t, h4.o, h4.h, h4.low, c4, h4.v)
    pick = [e for e in breakdowns(h1b, h4b, cfg) if e.at_ms == at]
    assert pick == [e for e in base if e.at_ms == at]


@pytest.mark.leakage
def test_breakdown_volume_average_excludes_current_bar() -> None:
    h1 = _h1_with_drop()
    k = 30
    # 현재 봉 거래량을 평균의 1.2 배 바로 위로: 평균에 현재 봉을 넣으면 탈락한다
    v = list(h1.v)
    v[k] = 1.21
    b = Bars(h1.t, h1.o, h1.h, h1.low, h1.c, v)
    ev = breakdowns(b, _h4_aligned(h1), Config(rsi_min=-1.0))
    assert any(e.at_ms == h1.t[k] + HOUR_MS for e in ev)


def test_trend_filter_blocks_uptrend() -> None:
    h1 = _h1_with_drop()
    h4 = _h4_aligned(h1)
    up = Bars(h4.t, h4.o, h4.h, h4.low, list(reversed(h4.c)), h4.v)
    assert breakdowns(h1, up, Config(rsi_min=-1.0)) == []


# ------------------------------------------------------------------ 매매


def _setup_bars(n: int = 60) -> Bars:
    return _flat(n)


def _ready(n: int = 60) -> Bars:
    """25 번 사건(수준 100) · 30 번 봉 거절 · 31 번 봉부터 99.7 에서 평평 (손절 · 익절 안 닿음)."""
    b = _flat(n)
    lvl = [100.0] * 31 + [99.7] * (n - 31)
    b = Bars(b.t, lvl, lvl, lvl, lvl, b.v)
    return _with(b, 30, o=100.1, h=100.2, lo=99.5, c=99.8)


def _with(b: Bars, k: int, *, o: float, h: float, lo: float, c: float) -> Bars:
    oo, hh, ll, cc = list(b.o), list(b.h), list(b.low), list(b.c)
    oo[k], hh[k], ll[k], cc[k] = o, h, lo, c
    return Bars(b.t, oo, hh, ll, cc, b.v)


def _event(b: Bars, k: int, level: float = 100.0) -> Breakdown:
    return Breakdown(b.t[k], level, level * 0.99, 2.0, 40.0)


def _cfg(**kw: float | bool) -> Config:
    base = {"fee": 0.0004, "slippage": 0.0002, "funding": False}
    base.update(kw)
    return Config(**base)  # type: ignore[arg-type]


def test_rejection_enters_next_open_with_slippage_and_stop_from_signal_atr() -> None:
    b = _setup_bars()
    b = _with(b, 30, o=100.1, h=100.2, lo=99.5, c=99.8)  # 띠 접촉 · 음봉 · 종가 < 수준
    b = _with(b, 31, o=99.7, h=99.7, lo=99.7, c=99.7)
    run = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    assert len(run.trades) == 1
    tr = run.trades[0]
    assert tr.signal_ms == b.t[30] and tr.entry_ms == b.t[31]
    assert math.isclose(tr.entry_price, 99.7 * (1 - 0.0002))
    assert math.isclose(tr.stop_price - tr.entry_price, tr.atr * 1.5)
    assert math.isclose(tr.entry_price - tr.take_profit, 2 * (tr.stop_price - tr.entry_price))
    assert math.isclose(tr.risk_amount, 10_000 * 0.005) or tr.capped


@pytest.mark.leakage
def test_entry_does_not_depend_on_future_bars() -> None:
    b = _ready()
    r1 = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    b2 = _with(b, 45, o=120.0, h=130.0, lo=119.0, c=125.0)
    r2 = simulate("X", b2, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    assert r1.trades[0].entry_ms == r2.trades[0].entry_ms
    assert r1.trades[0].entry_price == r2.trades[0].entry_price


def test_window_expires_after_eight_bars() -> None:
    b = _setup_bars()
    b = _with(b, 25 + 8, o=100.1, h=100.2, lo=99.5, c=99.8)  # 9 번째 봉: 창 밖
    run = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    assert run.trades == [] and run.expired == 1
    b = _with(_setup_bars(), 25 + 7, o=100.1, h=100.2, lo=99.5, c=99.8)  # 8 번째 봉
    run = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    assert len(run.trades) == 1


def test_bullish_or_closed_above_level_is_not_rejection() -> None:
    b = _with(_setup_bars(), 30, o=99.6, h=100.2, lo=99.5, c=99.8)  # 양봉
    assert simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1]).trades == []
    b = _with(_setup_bars(), 30, o=100.3, h=100.4, lo=99.9, c=100.1)  # 종가 > 수준
    assert simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1]).trades == []


def test_stop_first_when_both_hit_and_costs_in_pnl() -> None:
    b = _ready()
    b = _with(b, 32, o=99.7, h=200.0, lo=1.0, c=99.7)  # 손절 · 익절 둘 다
    run = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    tr = run.trades[0]
    assert tr.exit_reason == "stop" and tr.ambiguous
    assert math.isclose(tr.exit_price, tr.stop_price * 1.0002)
    gross = (tr.entry_price - tr.exit_price) * tr.quantity
    fees = (tr.entry_price + tr.exit_price) * tr.quantity * 0.0004
    assert math.isclose(tr.net_pnl, gross - fees)
    assert tr.r_multiple < -1  # 비용 · 미끄럼으로 −1R 보다 나쁘다
    # 1 분봉 판정기가 있으면 그 결과를 따른다
    run = simulate(
        "X",
        b,
        [_event(b, 25)],
        _cfg(),
        from_ms=b.t[0],
        to_ms=b.t[-1],
        resolver=lambda t, s, p: "take_profit",
    )
    assert run.trades[0].exit_reason == "take_profit"


def test_gap_through_stop_fills_at_open() -> None:
    b = _ready()
    b = _with(b, 32, o=110.0, h=111.0, lo=109.0, c=110.0)
    tr = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1]).trades[0]
    assert math.isclose(tr.exit_price, 110.0 * 1.0002)


def test_take_profit_needs_trade_through() -> None:
    b = _ready()
    probe = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1]).trades[0]
    tp = probe.take_profit
    touch = _with(b, 32, o=99.7, h=99.7, lo=tp, c=99.7)
    tr = simulate("X", touch, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1]).trades[0]
    assert tr.exit_reason == "end"
    through = _with(b, 32, o=99.7, h=99.7, lo=tp - 0.01, c=99.7)
    tr = simulate("X", through, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1]).trades[0]
    assert tr.exit_reason == "take_profit"
    assert math.isclose(tr.exit_price, tp * 1.0002)


def test_one_position_per_breakdown_and_busy_events_dropped() -> None:
    b = _ready(80)
    # 같은 사건으로 두 번째 거절(손절 · 익절에는 안 닿음) → 무시
    b = _with(b, 33, o=99.71, h=99.72, lo=99.6, c=99.65)
    b = _with(b, 40, o=99.7, h=150.0, lo=99.7, c=99.7)  # 손절
    b = _with(b, 41, o=99.7, h=99.7, lo=99.7, c=99.7)
    b = _with(b, 42, o=100.1, h=100.2, lo=99.5, c=99.8)  # 손절 뒤 같은 수준 거절 → 무시
    events = [_event(b, 25), _event(b, 35)]  # 두 번째 사건은 포지션 중 → 버림
    run = simulate("X", b, events, _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    assert len(run.trades) == 1 and run.busy == 1
    events.append(_event(b, 41))  # 손절 뒤 새 사건 → 새 거래 가능
    run = simulate("X", b, events, _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    assert len(run.trades) == 2 and run.trades[1].breakdown_ms == b.t[41]


def test_size_capped_at_three_times_balance() -> None:
    b = _setup_bars()
    b = _with(b, 30, o=100.1, h=100.2, lo=99.95, c=99.96)  # 평평한 봉이라 ATR 이 작다
    tr = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1]).trades[0]
    assert tr.capped
    assert math.isclose(tr.position_value, 30_000.0)
    assert tr.risk_amount < 50.0


def test_short_receives_positive_funding_only_when_held() -> None:
    b = _ready()
    fund = {b.t[31]: 0.01, b.t[35]: 0.001}  # 진입 봉 시각 정산은 받지 않는다
    tr = simulate(
        "X", b, [_event(b, 25)], DEFAULT, from_ms=b.t[0], to_ms=b.t[-1], funding=fund
    ).trades[0]
    assert math.isclose(tr.funding_fee, tr.quantity * b.o[35] * 0.001)


# ------------------------------------------------------------------ 성과


def test_streaks_drawdowns_and_summary() -> None:
    b = _ready()
    b = _with(b, 32, o=99.7, h=150.0, lo=99.7, c=99.7)
    run = simulate("X", b, [_event(b, 25)], _cfg(), from_ms=b.t[0], to_ms=b.t[-1])
    assert max_streak(run.trades, wins=False) == 1
    curve = daily_equity(run.trades, run.marks, 10_000.0, b.t[0], b.t[-1])
    s = summary(run.trades, curve, 10_000.0)
    assert s["total_trades"] == 1 and s["losing_trades"] == 1
    dd = drawdowns([(0, 100.0), (1, 80.0), (2, 90.0), (3, 101.0), (4, 95.0)])
    assert dd[0].depth == pytest.approx(0.2) and dd[0].recovery_ms == 3
    assert dd[1].recovery_ms is None
