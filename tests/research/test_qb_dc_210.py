# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 210 합성 검산 — 거래대금 봉 · 방향전환 인과성, 주문 · 부분 체결 · 미체결, 수수료 · funding, 스톱 우선 · 재진입, 경계."""

from __future__ import annotations

import math
from array import array

from scripts.run_qb_dc_210 import (
    DAY,
    H4,
    MIN,
    WARM,
    Market,
    Params,
    Signals,
    _vol_weight,
    daily_closes,
    dc_states,
    qb_bars,
    simulate,
)

PER = DAY // MIN


def market(prices: list[float], qv: list[float] | None = None) -> Market:
    n = len(prices)
    q = qv if qv is not None else [1e9] * n
    a = array("d", prices)
    return Market(
        array("d", prices),
        array("d", [x * 1.0001 for x in prices]),
        array("d", [x * 0.9999 for x in prices]),
        a,
        array("d", q),
        0,
    )


def test_qb_bar_target_fixed_overshoot_and_available_time() -> None:
    days = 32
    qv = [1.0] * (30 * PER) + [3.0] * (2 * PER)  # 30 일 동안 하루 1,440 → 목표 1,440
    m = market([100.0 + i * 1e-4 for i in range(days * PER)], qv)
    from scripts.run_qb_dc_210 import daily_quote

    bars = qb_bars(m, daily_quote(m))
    first_end = 30 * PER + 480 - 1  # 3 씩 더해 480 분째에 1,440 도달 (넘침 포함)
    assert bars[0][2] == WARM + 30 * PER * MIN
    assert bars[0][0] == WARM + (first_end + 1) * MIN  # 이용 가능 = 마지막 분이 끝난 시각
    assert bars[0][1] == m.c[first_end]
    assert bars[1][2] == bars[0][0]  # 다음 봉은 다음 분부터
    # 목표는 봉 시작 때 고정: 31 일째(거래대금 3 배) 시작 봉도 30 일 중앙값 기준
    # 인과성: 뒤 자료를 바꿔도 앞 봉은 그대로
    m2 = market(list(m.c), qv[: 31 * PER] + [50.0] * PER)
    assert qb_bars(m2, daily_quote(m2))[:3] == bars[:3]


def test_dc_states_causal_and_threshold() -> None:
    xs = [100, 96, 95, 99.74, 99.75, 104, 99, 98.8, 97]
    st = dc_states(xs)
    assert st == [
        0,
        0,
        0,
        0,
        1,
        1,
        1,
        0,
        0,
    ]  # 95 × 1.05 = 99.75 에서 LONG, 104 × 0.95 = 98.8 에서 FLAT
    for k in range(1, len(xs)):
        assert dc_states(xs[:k]) == st[:k]  # 앞부분만으로 같은 상태 (미래 극값 안 씀)


def _run(
    prices: list[float],
    sig: list[float],
    p: Params,
    qv: list[float] | None = None,
    funding=None,
    eval_day: int = 61,
):
    m = market(prices, qv)
    if funding:
        m.funding = funding
    n_j = len(prices) * MIN // H4
    s = Signals({"T20": (sig + [sig[-1]] * n_j)[:n_j]})
    return m, simulate({"X": m}, {"X": s}, "T20", p, eval_start=WARM + eval_day * DAY)


def test_entry_fill_at_open_with_cost_and_vol_weight() -> None:
    days = 64
    # 하루 안에서만 천천히 움직이는 가격 (분 사이 변화가 한도 0.3% 보다 작음), 일간 수익 ±1% 번갈아
    prices = []
    level = 100.0
    for d in range(days):
        step = (1.01 if d % 2 else 1 / 1.01) ** (1 / PER)
        for _ in range(PER):
            level *= step
            prices.append(level)
    p = Params()
    m, res = _run(prices, [1.0] * 1000, p)
    bk = res["book"]
    i0 = 61 * PER
    w = _vol_weight(daily_closes(m), 60, p)
    assert bk.start["X"] == i0
    q0 = w * 10_000 / m.c[i0 - 1]  # 목표 수량 = 비중 × equity ÷ 기준가(직전 분 종가)
    first_fill_cost = q0 * m.o[i0] * p.cost
    assert bk.pnl_cost["X"] <= -first_fill_cost * 0.999


def test_unfilled_when_open_beyond_limit_and_partial_fill() -> None:
    days = 63
    prices = [100.0] * (days * PER)
    i0 = 61 * PER
    for a in range(4):  # 판단 뒤 4 분 모두 시가가 +0.5% (한도 +0.3% 밖)
        prices[i0 + a] = 100.5
    _m, res = _run(prices, [1.0] * 1000, Params())
    bk = res["book"]
    assert bk.unfilled >= 1
    # 부분 체결: 한 분 거래대금이 작으면 1% 만큼만
    qv = [1e9] * (days * PER)
    for a in range(4):
        qv[i0 + a] = 10_000.0  # 1% = 100 USDT / 분
    _m2, res2 = _run([100.0] * (days * PER), [1.0] * 1000, Params(), qv)
    bk2 = res2["book"]
    assert bk2.traded_notional >= 400 - 1e-6
    assert bk2.unfilled >= 1


def test_funding_uses_mark_and_qty_before_orders() -> None:
    days = 63
    prices = [100.0] * (days * PER)
    t = WARM + 62 * DAY  # 62 일째 00:00 정산, 그 전에 보유
    _m0, res = _run(prices, [1.0] * 1000, Params(), funding=[(t, 0.001, 200.0)])
    bk = res["book"]
    assert math.isclose(bk.pnl_fund["X"], -bk.qty["X"] * 200.0 * 0.001, rel_tol=1e-9)
    _m, res2 = _run(prices, [1.0] * 1000, Params(use_mark=False), funding=[(t, 0.001, 200.0)])
    assert math.isclose(
        res2["book"].pnl_fund["X"], -res2["book"].qty["X"] * 100.0 * 0.001, rel_tol=1e-9
    )


def test_stop_priority_extra_cost_and_reentry_block() -> None:
    days = 64
    prices = [100.0] * (days * PER)
    crash = 62 * PER + 300  # 62 일째 05:00 에 −25%
    for i in range(crash, len(prices)):
        prices[i] = 75.0
    _m0, res = _run(prices, [1.0] * 1000, Params())
    bk = res["book"]
    assert (
        bk.stops == 1 and bk.blocked["X"] is True and bk.qty["X"] == 0.0
    )  # 신호가 계속 LONG 이라 다시 안 삼
    _s, _start, end, pnl = bk.trades[0]
    assert end == crash and pnl < 0
    assert bk.stop_extra_cost > 0  # 비상 추가 불리폭 1% 따로 기록
    # 신호가 한 번 꺼졌다 켜지면 다시 산다
    n_j = len(prices) * MIN // H4
    j_off = (62 * DAY + 8 * 3_600_000) // H4
    sig = [1.0] * n_j
    sig[j_off] = 0.0
    _m, res2 = _run(prices, sig, Params())
    assert res2["book"].qty["X"] > 0


def test_eval_boundary_no_trading_before_start() -> None:
    days = 63
    prices = [100.0] * (days * PER)
    _m, res = _run(prices, [1.0] * 1000, Params(), eval_day=62)
    bk = res["book"]
    assert bk.start["X"] == 62 * PER  # 평가 시작 판단 시각 첫 분에 처음 진입
    assert res["daily"][0][1] == 10_000.0
