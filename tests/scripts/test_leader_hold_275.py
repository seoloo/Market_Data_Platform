"""문서 275 — 6 달 나눠 사기 엔진 · 변동성 비중 · 판정 (순수 함수)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts import run_leader_hold_275 as h


def _days(n: int) -> list[str]:
    # 달마다 3 거래일: 1 · 2 · 3 일 → 월말 = 3 일
    out: list[str] = []
    y, mo = 2016, 1
    while len(out) < n:
        out += [f"{y}-{mo:02d}-0{d}" for d in (1, 2, 3)]
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out[:n]


def test_tranche_buys_slot_share_next_day_and_sells_after_hold() -> None:
    days = _days(12)
    close: dict[str, list[float | None]] = {"A": [10.0] * 12}
    close["A"][5] = 20.0  # 산 뒤 오름
    close["A"][6] = 20.0
    r = h.simulate_hold(days, close, {2: ["A"]}, "2016-01-01", fee=0.0, hold=3, slots=2)
    # 월말(인덱스 2) 고름 → 3 에서 계좌 1/2 = 0.5 를 10 에 삼 → 6 에서 20 에 팖
    assert r.equity[3] == pytest.approx(1.0)
    assert r.equity[5] == pytest.approx(1.5)
    assert r.trades == [pytest.approx(1.0)]
    assert r.equity[-1] == pytest.approx(1.5)  # 판 뒤 현금 그대로


def test_fee_and_market_filter() -> None:
    days = _days(9)
    close: dict[str, list[float | None]] = {"A": [10.0] * 9}
    r = h.simulate_hold(days, close, {2: ["A"]}, "2016-01-01", fee=0.01, hold=3, slots=1)
    assert r.trades == [pytest.approx(0.99 * 0.99 - 1)]
    off = h.simulate_hold(
        days, close, {2: ["A"]}, "2016-01-01", fee=0.01, hold=3, slots=1, buy_ok=lambda t: False
    )
    assert off.trades == [] and off.equity[-1] == 1.0


def test_missing_price_on_buy_day_keeps_cash() -> None:
    days = _days(9)
    close: dict[str, list[float | None]] = {"A": [10.0] * 9, "B": [10.0] * 9}
    close["B"][3] = None
    r = h.simulate_hold(days, close, {2: ["A", "B"]}, "2016-01-01", fee=0.0, hold=3, slots=1)
    assert len(r.trades) == 1  # B 는 못 삼


def test_inverse_vol_gives_calm_stock_more_weight() -> None:
    n = 80
    calm = [100.0 + (i % 2) * 0.1 for i in range(n)]
    wild = [100.0 + (i % 2) * 5.0 for i in range(n)]
    w = h.inverse_vol({"C": calm, "W": wild})(n - 1, ["C", "W"])
    assert sum(w) == pytest.approx(1.0) and w[0] > 0.9


def _res(g: float, b: float, spy: float, mdd: float = 0.3) -> dict[str, Any]:
    def acc(s: float, d: float) -> dict[str, Any]:
        return {"full": {"sharpe": s, "mdd": d}, "first": {"sharpe": s}, "second": {"sharpe": s}}

    return {"arms": {"A_EQ": {"GBM": acc(g, mdd), "BASE": acc(b, 0.3)}}, "spy": acc(spy, 0.3)}


def test_judge() -> None:
    good = _res(1.0, 0.8, 0.9)
    assert h.judge({"U1": good, "U2": good}, "A_EQ")["status"] == "후속 가치(탐색)"
    assert h.judge({"U1": good, "U2": _res(1.0, 0.8, 1.1)}, "A_EQ")["status"] == "불명"
    assert h.judge({"U1": _res(0.7, 0.8, 0.5), "U2": _res(0.6, 0.8, 0.5)}, "A_EQ")["status"] == (
        "불리"
    )


def test_zero_budget_month_buys_nothing() -> None:
    days = _days(12)
    close: dict[str, list[float | None]] = {"A": [10.0] * 12}
    # slots=1: 첫 달에 현금을 다 씀 → 둘째 달 예산 0 이어도 0 으로 나누지 않는다
    r = h.simulate_hold(days, close, {2: ["A"], 5: ["A"]}, "2016-01-01", fee=0.0, hold=6, slots=1)
    assert len(r.trades) == 1 and r.equity[-1] == pytest.approx(1.0)
