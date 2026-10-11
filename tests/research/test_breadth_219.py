# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 219 합성 검산 — 참여율(같은 유효 종목 · 0 은 상승 아님 · 결측 · 달 경계) · 신호 · 진입 시각 · 상한 · 보호 · 24 시간 청산 · funding · 수량 · 대조 선택."""

from __future__ import annotations

from array import array
from datetime import date

from scripts.run_breadth_219 import (
    H4,
    HOLD_MIN,
    P,
    Row,
    _ms,
    account,
    is_a,
    match_controls,
    participation,
    run_trade,
)
from scripts.run_qb_dc_210 import MIN, WARM, Market

T = _ms(date(2024, 3, 1))
N_MIN = (_ms(date(2026, 9, 1)) - WARM) // MIN


def _bars(closes: list[float], t0: int) -> dict[int, tuple[float, float, float, float]]:
    return {t0 + k * H4: (c, c, c, c) for k, c in enumerate(closes)}


def test_participation_same_valid_set_zero_not_up_and_missing() -> None:
    t0 = T - 2 * H4
    bars = {}
    for k in range(15):
        bars[f"U{k}"] = _bars([100, 99, 101], t0)  # 직전 하락 → 이번 상승
    bars["Z"] = _bars([100, 100, 100], t0)  # 0 수익은 상승 아님
    bars["M"] = {t0: (1, 1, 1, 1.0), t0 + 2 * H4: (1, 1, 1, 2.0)}  # 봉 t−1 없음 → 유효 아님
    members = {"2024-03": [*bars], "2024-02": ["U0"]}
    btc = _bars([100.0] * 44 + [101.0], T - 44 * H4)
    rows = participation(members, bars, btc, start=T, end=T + H4)
    r = rows[0]
    assert r.n_valid == 16 and r.b_prev == 0.0 and r.b_cur == 15 / 16
    assert r.btc_ret is not None and r.btc_ret > 0 and r.btc_low == 101.0
    few = participation({"2024-03": ["U0", "U1"]}, bars, btc, start=T, end=T + H4)[0]
    assert few.b_cur is None  # 유효 < 15 → 참여율 없음


def test_month_boundary_uses_current_month_members_for_both() -> None:
    t = _ms(date(2024, 3, 1))  # 3 월 첫 봉: 직전 봉은 2 월이지만 3 월 종목군으로 셈
    bars = {f"N{k}": _bars([100, 99, 101], t - 2 * H4) for k in range(16)}
    bars["OLD"] = _bars([100, 101, 90], t - 2 * H4)
    members = {"2024-03": [f"N{k}" for k in range(16)], "2024-02": ["OLD"]}
    r = participation(members, bars, {}, start=t, end=t + H4)[0]
    assert r.n_valid == 16 and r.b_prev == 0.0 and r.b_cur == 1.0


def _row(b_prev: float, b_cur: float, btc_ret: float, t: int = T) -> Row:
    return Row(t, 20, b_prev, b_cur, 0.01, btc_ret, 0.01, 100.0, 99.0)


def test_signal_conditions_inclusive() -> None:
    assert is_a(_row(0.30, 0.80, 0.001))
    assert not is_a(_row(0.31, 0.95, 0.01))
    assert not is_a(_row(0.10, 0.79, 0.01))
    assert not is_a(_row(0.10, 0.90, 0.0))  # BTC 수익 0 은 아님


def _market(
    o_at: dict[int, float], low_at: dict[int, float] | None = None, base: float = 100.0
) -> Market:
    o = array("d", [base]) * N_MIN
    for i, v in o_at.items():
        o[i] = v
    h = array("d", o)
    lo = array("d", o)
    for i, v in (low_at or {}).items():
        lo[i] = v
    c = array("d", o)
    qv = array("d", [1e12]) * N_MIN
    return Market(o, h, lo, c, qv, 0)


def test_entry_next_minute_limit_cap_and_24h_exit() -> None:
    ts = T + H4
    i_sig = (ts - WARM) // MIN
    m = _market({i_sig: 90.0, i_sig + 1: 100.2, i_sig + 1 + HOLD_MIN: 105.0})
    tr = run_trade(m, [], ts, 100.0, 95.0, 1.0, P(), account=False)
    assert (
        tr.first_fill == i_sig + 1 and tr.fills[0][2] == 100.2
    )  # 신호 분이 아니라 다음 분, 상한 100.3 안
    assert tr.exit_i == i_sig + 1 + HOLD_MIN and tr.reason == "시간 청산"
    assert abs(tr.parts()["gross"] - 4.8) < 1e-9
    hi = _market(dict.fromkeys(range(i_sig + 1, i_sig + 30), 100.5))
    t2 = run_trade(hi, [], ts, 100.0, 95.0, 1.0, P(), account=False)
    assert (
        not t2.fills and t2.reason == "미체결"
    )  # 상한 100.3 위 시가는 체결 안 됨 (닿았다고 체결하지 않음)


def test_abandon_if_price_below_stop_before_entry_and_stop_fill() -> None:
    ts = T + H4
    i0 = (ts - WARM) // MIN + 1
    m = _market({i0: 100.5, i0 + 1: 94.0}, base=100.5)  # 첫 두 분 상한 위, 그 다음 보호가 아래
    tr = run_trade(m, [], ts, 100.0, 95.0, 1.0, P(), account=False)
    assert not tr.fills and tr.reason == "진입 전 보호가 이하"
    m2 = _market({i0 + 5: 96.0}, low_at={i0 + 5: 94.0})
    t2 = run_trade(m2, [], ts, 100.0, 95.0, 1.0, P(), account=False)
    assert t2.reason == "보호 청산" and t2.exit_i == i0 + 5
    assert (
        t2.fills[-1][2] == 95.0 and abs(t2.fills[-1][4] - 95.0 * 0.01) < 1e-9
    )  # min(보호가, 시가) + 비상 1 %


def test_funding_sign_long_pays() -> None:
    ts = T + H4
    i0 = (ts - WARM) // MIN + 1
    m = _market({})
    m.funding = [(WARM + (i0 + 100) * MIN, 0.001, 100.0)]
    tr = run_trade(m, [m.funding[0][0]], ts, 100.0, 95.0, 2.0, P(), account=False)
    assert tr.funds == [(i0 + 100, 2.0 * 100.0 * 0.001)]  # 롱 · 양수 rate → 냄
    assert tr.parts()["funding"] > 0


def test_account_sizing_min_notional_and_busy_skip() -> None:
    m = _market({})
    r1 = Row(
        T, 20, 0.1, 0.9, 0.01, 0.01, 0.01, 100.0, 99.0
    )  # 거리 1 → 위험 25 / 1 = 25, 명목 상한 2,500 / 100 = 25
    r2 = Row(T + H4, 20, 0.1, 0.9, 0.01, 0.01, 0.01, 100.0, 99.0)  # 보유 중
    res = account(m, [r1, r2], P(), start=T - 86_400_000, end=T + 5 * 86_400_000)
    assert len(res["trades"]) == 1 and res["skips"]["보유 중 신호"] == 1
    assert abs(res["trades"][0].want - 25.0) < 1e-9
    r3 = Row(
        T, 20, 0.1, 0.9, 0.01, 0.01, 0.01, 100.0, 50.0
    )  # 거리 50 → 0.5 × 100 = 50 USDT ≥ 최소 명목 50
    small = account(m, [r3], P(equity0=100.0), start=T - 86_400_000, end=T + 3 * 86_400_000)
    assert small["skips"]["수량 미달(사전 검사)"] == 1  # 0.25 % × 100 / 50 = 0.005 → 명목 0.5 < 50


def test_match_controls_excludes_a_and_nearby() -> None:
    rows = [
        Row(T + k * H4, 20, 0.6, 0.7, 0.01 + k * 1e-4, 0.01, 0.01, 100.0, 99.0) for k in range(40)
    ]
    rows[20] = Row(T + 20 * H4, 20, 0.1, 0.9, 0.012, 0.01, 0.01, 100.0, 99.0)  # A
    ctrl = match_controls(rows, k=3)
    picked = ctrl[T + 20 * H4]
    assert all(abs(t - (T + 20 * H4)) >= 12 * 3_600_000 for t in picked)
    assert len(picked) == 3
