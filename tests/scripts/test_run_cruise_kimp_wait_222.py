"""문서 222 — 김프 계산 · 대기 날짜 · 매수 건너뛰기 · 판정 규칙."""

from __future__ import annotations

from datetime import date, timedelta

from scripts.run_cruise_kimp_wait_222 import blocks, premium, verdict
from scripts.run_cruise_range_197 import simulate_cruise


def test_premium_and_blocks() -> None:
    u = {"BTC": {"d1": 105.0, "d2": 101.0}}
    n = {"BTC": {"d1": 100.0, "d2": 100.0}}
    k = premium(u, n)
    assert abs(k["BTC"]["d1"] - 0.05) < 1e-12
    assert blocks(k) == {"BTC": {"d1"}}


def _px(n: int) -> tuple[list[str], dict[str, dict[str, float]]]:
    d0 = date(2020, 1, 1)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    return days, {"BTC": {d: 100.0 * (1.003**i) for i, d in enumerate(days)}}


def test_buy_block_skips_buys_only() -> None:
    days, px = _px(300)
    base = simulate_cruise(days, px, start=days[150], tax=False)
    blocked = simulate_cruise(days, px, start=days[150], tax=False, buy_block={"BTC": set(days)})
    assert base["trades"] > 0
    assert blocked["trades"] == 0
    same = simulate_cruise(days, px, start=days[150], tax=False, buy_block={"BTC": set()})
    assert same["vals"] == base["vals"]


def test_verdict_rules() -> None:
    good = {"d_irr": 0.01, "d_mdd": 0.0}
    bad = {"d_irr": -0.01, "d_mdd": 0.0}
    w_ok = {"win_share": 0.7, "median_d_irr": 0.002}
    w_no = {"win_share": 0.5, "median_d_irr": 0.002}
    assert verdict(good, good, w_ok) == "지지"
    assert verdict(good, good, w_no) == "불명"
    assert verdict(bad, bad, w_ok) == "불리"
    assert verdict(good, bad, w_ok) == "불명"
