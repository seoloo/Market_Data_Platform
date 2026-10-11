"""문서 217 — 사건 · 신호 · 일봉 근사 체결 · 보호 · 추적 · funding · 계좌 한도 검산."""

from __future__ import annotations

from dataclasses import replace

import pytest

from auto_trading.research.reclaim_217 import (
    DAY,
    Bar,
    Candidate,
    Config,
    Episode,
    Signal,
    Trade,
    arm_signals,
    cluster_bootstrap,
    find_episodes,
    random_signal,
    replay,
    simulate_trade,
)

T0 = 1_600_000_000_000 // DAY * DAY
CFG = Config()
QV = 1e12  # 거래대금 제한이 걸리지 않게


def _bars(closes: list[float], *, lows: list[float] | None = None) -> list[Bar]:
    out = []
    prev = closes[0]
    for k, c in enumerate(closes):
        lo = lows[k] if lows is not None else min(prev, c) * 0.99
        out.append(Bar(T0 + k * DAY, prev, max(prev, c) * 1.01, lo, c, QV))
        prev = c
    return out


def _crash_then(after: list[float]) -> list[Bar]:
    """90 일 100 → 사건 시작 65 (−35%) → after."""
    return _bars([100.0] * 90 + [65.0] + after)


def test_episode_trigger_and_end_on_recovery() -> None:
    bars = _crash_then([60.0] * 3 + [80.0, 101.0] + [100.0] * 5)
    eps = find_episodes("X", bars, lambda t: True, CFG)
    assert len(eps) == 1
    ep = eps[0]
    assert (ep.i0, ep.p, ep.h) == (90, 65.0, 100.0)
    assert bars[ep.end].c >= 100.0 and ep.end == 95


def test_episode_needs_membership_and_lifetime() -> None:
    bars = _crash_then([60.0] * 200)
    assert find_episodes("X", bars, lambda t: False, CFG) == []
    ep = find_episodes("X", bars, lambda t: True, CFG)[0]
    assert ep.end == ep.i0 + 180
    # 180 일 뒤 90 일 최고 종가는 60 이라 새 사건이 아니다
    assert len(find_episodes("X", bars, lambda t: True, CFG)) == 1
    # 사건 진행 중 더 떨어져도 새 사건을 만들지 않고, 끝난 다음 날부터 다시 셈
    deeper = _crash_then([60.0] * 10 + [40.0] * 175 + [20.0])
    eps = find_episodes("X", deeper, lambda t: True, CFG)
    assert len(eps) == 2 and eps[0].end == 270 and deeper[eps[1].i0].c == 20.0


def test_arm_signals_definitions() -> None:
    # 저점 55 (사건 뒤 2 일째), 그 뒤 5 일 새 저점 없음, 66 > P 65 에서 A
    after = [60.0, 55.0, 58.0, 59.0, 60.0, 61.0, 62.0, 66.0, 67.0] + [67.0] * 30
    lows = (
        [100.0 * 0.99] * 90
        + [64.0, 59.0, 54.0, 57.0, 58.0, 59.0, 60.0, 61.0, 64.0, 66.0]
        + [66.0] * 30
    )
    bars = _bars([100.0] * 90 + [65.0] + after, lows=lows)
    ep = find_episodes("X", bars, lambda t: True, CFG)[0]
    s = arm_signals(bars, ep, CFG)
    assert s["I"].i == 90 and s["I"].stop == 64.0
    low_i = 92
    assert s["D"].i == low_i + 5 and s["D"].stop == 54.0
    assert s["A"].i == 98 and bars[98].c > ep.p and s["A"].stop == 54.0
    assert s["T"].i > 90 and bars[s["T"].i].c > bars[s["T"].i - 20].c
    r = random_signal(bars, ep, 3)
    assert r.i == 93 and r.stop == 54.0


@pytest.mark.leakage
def test_signals_use_only_bars_up_to_signal_day() -> None:
    after = [60.0, 55.0, 58.0, 59.0, 60.0, 61.0, 62.0, 66.0] + [66.0] * 20
    a = _crash_then(after)
    ep = find_episodes("X", a, lambda t: True, CFG)[0]
    sa = arm_signals(a, ep, CFG)["A"]
    b = a[: sa.i + 1] + [
        Bar(x.t, 1.0, 1.0, 0.5, 0.5, QV) for x in a[sa.i + 1 :]
    ]  # 신호 뒤 자료를 망가뜨려도
    sb = arm_signals(b, Episode("X", ep.i0, sa.i, ep.p, ep.h), CFG)["A"]
    assert (sa.i, sa.stop) == (sb.i, sb.stop)


def _sig(bars: list[Bar], i: int, stop: float) -> Signal:
    return Signal("A", "X", Episode("X", i - 5, i + 200, 50.0, 100.0), i, stop)


def _flat(n: int, c: float = 100.0) -> list[Bar]:
    return [Bar(T0 + k * DAY, c, c * 1.01, c * 0.99, c, QV) for k in range(n)]


def test_entry_open_fill_size_and_cost() -> None:
    bars = _flat(30)
    bars.append(Bar(T0 + 30 * DAY, 100.0, 101.0, 99.5, 100.5, QV))  # 진입일
    bars += [Bar(T0 + k * DAY, 100.5, 101.5, 99.6, 100.5, QV) for k in range(31, 40)]
    tr = simulate_trade(bars, _sig(bars, 29, 90.0), 10_000.0, CFG, last_eval_day=bars[-1].t)
    assert isinstance(tr, Trade)
    assert tr.entry_kind == "open" and tr.entry_px == 100.0
    assert tr.qty == pytest.approx(0.0025 * 10_000 / (100 * 0.10))
    assert tr.open_at_end and tr.exit_reason == "open_at_end"
    assert tr.fees == pytest.approx(tr.qty * 100.0 * 0.00095)


def test_entry_limit_unfilled_and_gap_invalid() -> None:
    base = _flat(30)
    lim = 100.0 * 1.003
    hi = [*base, Bar(T0 + 30 * DAY, 101.0, 102.0, 100.0, 101.0, QV), *_flat(5)]
    tr = simulate_trade(hi, _sig(hi, 29, 90.0), 10_000.0, CFG, last_eval_day=hi[-1].t)
    assert isinstance(tr, Trade) and tr.entry_kind == "limit"
    assert tr.entry_px == pytest.approx(lim)
    away = [*base, Bar(T0 + 30 * DAY, 101.0, 102.0, 100.5, 101.0, QV)]
    assert simulate_trade(away, _sig(away, 29, 90.0), 1e4, CFG, last_eval_day=0) == "unfilled"
    gap = [*base, Bar(T0 + 30 * DAY, 89.0, 92.0, 88.0, 91.0, QV)]
    assert simulate_trade(gap, _sig(gap, 29, 90.0), 1e4, CFG, last_eval_day=0) == "gap_invalid"


def test_same_day_stop_after_fill_pays_emergency() -> None:
    bars = [*_flat(30), Bar(T0 + 30 * DAY, 100.0, 100.5, 89.0, 95.0, QV), *_flat(3)]
    tr = simulate_trade(bars, _sig(bars, 29, 90.0), 1e4, CFG, last_eval_day=bars[-1].t)
    assert isinstance(tr, Trade) and tr.exit_reason == "stop"
    assert tr.exit_px == 90.0
    assert tr.emergency == pytest.approx(tr.qty * 90.0 * 0.01)
    assert tr.r < -1.0  # 1R 손실 + 비용


def test_gap_through_stop_fills_at_open() -> None:
    bars = [*_flat(31), Bar(T0 + 31 * DAY, 80.0, 82.0, 79.0, 81.0, QV), *_flat(2)]
    tr = simulate_trade(bars, _sig(bars, 29, 90.0), 1e4, CFG, last_eval_day=bars[-1].t)
    assert isinstance(tr, Trade) and tr.exit_px == 80.0 and tr.exit_reason == "stop"


def test_trailing_exit_next_day_limit() -> None:
    up = [100.0 + 5 * k for k in range(10)]  # 100 → 145
    bars = _flat(30)
    for k, c in enumerate(up):
        bars.append(Bar(T0 + (30 + k) * DAY, c, c * 1.01, c * 0.995, c, QV))
    t = T0 + 40 * DAY
    bars.append(Bar(t, 145.0, 146.0, 100.0, 101.0, QV))  # 종가가 추적선 아래
    bars.append(Bar(t + DAY, 101.0, 102.0, 100.0, 101.0, QV))
    tr = simulate_trade(bars, _sig(bars, 29, 90.0), 1e4, CFG, last_eval_day=bars[-1].t)
    assert isinstance(tr, Trade)
    assert tr.exit_reason == "trail" and tr.exit_t == t + DAY and tr.exit_px == 101.0


def test_time_exit_after_max_hold() -> None:
    cfg = replace(CFG, max_hold=3)
    bars = [*_flat(35)]
    tr = simulate_trade(bars, _sig(bars, 29, 90.0), 1e4, cfg, last_eval_day=bars[-1].t)
    assert isinstance(tr, Trade) and tr.exit_reason == "time"
    assert tr.exit_t == bars[34].t  # 진입 30 + 3 일 종가 → 34 일 시가


def test_delisting_closes_at_last_close_with_emergency() -> None:
    bars = _flat(33)
    tr = simulate_trade(bars, _sig(bars, 29, 90.0), 1e4, CFG, last_eval_day=bars[-1].t + 10 * DAY)
    assert isinstance(tr, Trade) and tr.exit_reason == "delisted"
    assert tr.emergency == pytest.approx(tr.qty * 100.0 * 0.01)


def test_funding_window_and_sign() -> None:
    bars = _flat(34)
    fund = [(bars[30].t, 0.001), (bars[30].t + 8 * 3_600_000, 0.001), (bars[31].t, -0.002)]
    tr = simulate_trade(
        bars, _sig(bars, 29, 90.0), 1e4, CFG, funding=fund, last_eval_day=bars[-1].t
    )
    assert isinstance(tr, Trade)
    # 시가 진입: 진입 시각(00:00) 정산은 빼고 08:00 · 다음 날 00:00 만
    assert tr.funding == pytest.approx(tr.qty * 100.0 * (0.001 - 0.002))
    off = simulate_trade(
        bars,
        _sig(bars, 29, 90.0),
        1e4,
        replace(CFG, use_funding=False),
        funding=fund,
        last_eval_day=bars[-1].t,
    )
    assert isinstance(off, Trade) and off.funding == 0.0


def test_replay_capacity_same_symbol_and_daily_loss() -> None:
    bars = {s: _flat(60) for s in "ABCDEF"}

    def sim(sig: Signal, eq: float) -> Trade | str:
        return simulate_trade(bars[sig.symbol], sig, eq, CFG, last_eval_day=T0 + 59 * DAY)

    def cand(s: str, i: int, p: float) -> Candidate:
        sig = Signal("A", s, Episode(s, 20, 200, 50.0, 100.0), i, 90.0)
        return Candidate(sig, T0 + (i + 1) * DAY, p)

    cands = [cand(s, 29, p) for s, p in zip("ABCDEF", (6, 5, 4, 3, 2, 1), strict=True)]
    cands.append(cand("A", 30, 9))  # 다음 날 같은 종목 → 보유 중이라 건너뜀
    days = [T0 + k * DAY for k in range(28, 60)]
    res = replay(cands, sim, days, CFG)
    taken = res["trades"]
    assert isinstance(taken, list)
    assert [t.symbol for t in taken] == ["A", "B", "C", "D"]
    assert res["skips"] == {"capacity": 2, "same_symbol": 1}


def test_cluster_bootstrap_deterministic() -> None:
    items = [(1, 1.0), (1, 2.0), (2, -1.0), (3, 0.5)]
    a = cluster_bootstrap(items, reps=200)
    b = cluster_bootstrap(items, reps=200)
    assert a == b and a[0] == pytest.approx(0.625) and a[1] <= a[0] <= a[2]


def test_suspension_gap_splits_series_and_closes_as_data_end() -> None:
    from auto_trading.research.reclaim_217 import split_segments

    old = _flat(33, 85.0)
    new = [Bar(old[-1].t + (22 + k) * DAY, 1.4, 1.5, 1.3, 1.4, QV) for k in range(10)]
    segs = split_segments([*old, *new])
    assert [len(x) for x in segs] == [33, 10]
    assert split_segments(_flat(10)) == [_flat(10)]
    short_gap = [*_flat(5), *[Bar(T0 + (5 + 3 + k) * DAY, 100, 101, 99, 100, QV) for k in range(3)]]
    assert len(split_segments(short_gap)) == 1  # 2 ~ 3 일 빠짐은 끊지 않음
    tr = simulate_trade(segs[0], _sig(segs[0], 29, 76.5), 1e4, CFG, last_eval_day=new[-1].t)
    assert isinstance(tr, Trade) and tr.exit_reason == "delisted" and tr.exit_px == 85.0
