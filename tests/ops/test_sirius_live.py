"""✨ Sirius 순수 계산 — 결정 시점 · 126 거래일 · 묶음 예산 · 넘기기(roll) · 장부."""

from __future__ import annotations

from typing import Any

import pytest

from auto_trading.ops import sirius_live as s

DAYS = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]


def _state(cash: float = 600.0) -> dict[str, Any]:
    st = s.new_state("2026-01-01")
    s.deposit(st, "2026-01-01", 840_000, cash, 1400)
    return st


def test_month_due_only_on_new_month_without_decision() -> None:
    assert s.month_due({}, "2026-10-30", "2026-11-02") == "2026-10-30"
    assert s.month_due({"2026-10-30": {}}, "2026-10-30", "2026-11-02") is None
    assert s.month_due({}, "2026-11-02", "2026-11-03") is None


def test_age_counts_sessions_after_buy_including_today() -> None:
    bars = ["2026-11-02", "2026-11-03", "2026-11-04"]
    assert s.age("2026-11-02", bars, "2026-11-05") == 3
    assert s.age("2026-11-02", bars, "2026-11-02") == 0


def test_first_tranche_buys_one_sixth_split_evenly() -> None:
    st = _state(600.0)
    picks = [f"S{i}" for i in range(10)]
    prices = dict.fromkeys(picks, 10.0)
    orders = s.plan(st, "2026-02-02", DAYS[:20], prices, picks, "2026-01-30")
    assert [o["kind"] for o in orders] == ["buy"] * 10
    assert all(o["usd"] == 10.0 for o in orders)  # 600 ÷ 6 ÷ 10


def test_buy_fill_creates_lot_and_due_after_hold_sells_all() -> None:
    st = _state(600.0)
    o = {"sym": "MU", "kind": "buy", "side": "BUY", "usd": 10.0, "lots": [], "tranche": "T1"}
    s.apply(st, o, "2026-02-02", 0.5, 10.0, 0.01)
    assert st["cash_usd"] == pytest.approx(589.99)
    lot = st["lots"][0]
    assert lot["qty"] == 0.5 and lot["cost_usd"] == pytest.approx(10.01)
    bars = [d for d in DAYS if d >= "2026-02-02"][: s.HOLD + 1]  # 산 날 + 126 거래일째
    today = bars[-1]
    bars = bars[:-1]
    sells = s.plan(st, today, bars, {"MU": 30.0}, None, None)
    assert sells == [{"sym": "MU", "kind": "sell", "side": "SELL", "qty": 0.5, "lots": ["T1:MU"],
                      "tranche": None, "ref_price": 30.0}]  # fmt: skip
    s.apply(st, sells[0], today, 0.5, 15.0, 0.02)
    assert st["lots"] == [] and st["cash_usd"] == pytest.approx(589.99 + 14.98)
    assert st["closed"][0]["ret"] == pytest.approx(14.98 / 10.01 - 1)


def test_not_due_before_hold() -> None:
    st = _state()
    st["lots"].append({"id": "T1:MU", "sym": "MU", "qty": 1.0, "buy_day": "2026-02-02",
                       "cost_usd": 10.0, "tranche": "T1"})  # fmt: skip
    bars = [d for d in DAYS if d >= "2026-02-02"][: s.HOLD]  # 125 거래일째 — 아직
    assert s.plan(st, bars[-1], bars[:-1], {"MU": 10.0}, None, None) == []


def test_roll_buys_only_difference_and_reopens_lot() -> None:
    st = _state(0.0)
    st["lots"].append({"id": "T1:MU", "sym": "MU", "qty": 1.0, "buy_day": "2026-01-02",
                       "cost_usd": 4.0, "tranche": "T1"})  # fmt: skip
    st["cash_usd"] = 56.0  # 평가 = 56 + 4(MU 1 주 × 4) = 60 → 몫 10 → 종목당 1(10 종목)
    picks = ["MU", *[f"S{i}" for i in range(9)]]
    prices = dict.fromkeys(picks, 1.0) | {"MU": 4.0}
    bars = [d for d in DAYS if d >= "2026-01-02"][: s.HOLD + 1]
    orders = s.plan(st, bars[-1], bars[:-1], prices, picks, "T7")
    roll = next(o for o in orders if o["sym"] == "MU")
    assert roll["kind"] == "roll" and roll["side"] == "SELL"  # 몫 1 < 보유 4 → 3 달러어치 판다
    assert roll["qty"] == pytest.approx(0.75)
    s.apply(st, roll, bars[-1], 0.75, 3.0, 0.0)
    lot = next(x for x in st["lots"] if x["sym"] == "MU")
    assert lot["id"] == "T7:MU" and lot["qty"] == pytest.approx(0.25) and lot["buy_day"] == bars[-1]
    assert st["closed"][0]["value_usd"] == pytest.approx(4.0)


def test_unfilled_roll_keeps_old_lot_for_retry() -> None:
    st = _state(0.0)
    st["lots"].append({"id": "T1:MU", "sym": "MU", "qty": 1.0, "buy_day": "2026-01-02",
                       "cost_usd": 4.0, "tranche": "T1"})  # fmt: skip
    o = {"sym": "MU", "kind": "roll", "side": "BUY", "usd": 5.0, "lots": ["T1:MU"],
         "tranche": "T7", "ref_price": 4.0}  # fmt: skip
    s.apply(st, o, "2026-07-01", 0.0, 0.0, 0.0)
    assert st["lots"][0]["id"] == "T1:MU"


def test_client_id_fits_toss_pattern() -> None:
    cid = s.client_id("2026-11-02", "BRK.B", "BUY")
    assert cid == "SIR-2026-11-02-BRK_B-B" and len(cid) <= 36
