"""문서 223 — 현금 두는 곳만 바꾼 QS 가 110 시뮬레이터와 같은지 · AGG 보유 · 판정 규칙."""

from __future__ import annotations

import math
from datetime import date, timedelta

from scripts.run_qs_park_223 import simulate_park, verdict
from scripts.run_qs_switch import simulate


def _inputs(n: int = 400) -> tuple[list[str], dict[str, float], dict[str, float]]:
    d0 = date(2010, 1, 4)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    qqq = {d: 100 * (1 + 0.3 * math.sin(i / 40)) * (1.0005**i) for i, d in enumerate(days)}
    rf = dict.fromkeys(days, 2.0)
    return days, qqq, rf


def test_cash_modes_match_qs_simulator() -> None:
    days, qqq, rf = _inputs()
    lev = {d: v * 1.1 for d, v in qqq.items()}
    sig = {d: (i // 50) % 2 == 0 for i, d in enumerate(days)}
    agg = dict.fromkeys(days, 50.0)
    kw = {"start": days[10], "end": days[-1]}
    for park, interest in (("cash0", False), ("tbill", True)):
        ref = simulate(
            days,
            qqq,
            lev,
            sig,
            rf,
            mode="QS",
            start=kw["start"],
            end=kw["end"],
            cash_interest=interest,
        )
        got = simulate_park(days, qqq, lev, sig, rf, agg, park=park, **kw)
        assert got.final == ref.final
        assert got.flows == ref.flows


def test_agg_mode_holds_bonds_while_off() -> None:
    days, qqq, rf = _inputs()
    lev = dict(qqq)
    sig = dict.fromkeys(days, False)  # 계속 꺼짐
    agg = {d: 50.0 * (1.001**i) for i, d in enumerate(days)}
    kw = {"start": days[0], "end": days[-1]}
    cash = simulate_park(days, qqq, lev, sig, rf, agg, park="cash0", **kw)
    bond = simulate_park(days, qqq, lev, sig, rf, agg, park="agg", **kw)
    assert cash.final == cash.invested
    assert bond.final > cash.final


def test_verdict() -> None:
    assert verdict(0.003, 0.7, 0.001, 0.0) == "지지"
    assert verdict(0.003, 0.5, 0.001, 0.0) == "불명"
    assert verdict(-0.001, 0.3, -0.001, 0.0) == "불리"
    assert verdict(0.003, 0.7, 0.001, 0.05) == "불명"
