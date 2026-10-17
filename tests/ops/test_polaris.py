"""🧭 Polaris 순수 계산 — 사기만 하는 맞추기 · 장부 · 회차 · 글."""

from __future__ import annotations

import pytest

from auto_trading.ops import polaris as m

ASKS = {"SPYM": 89.68, "QQQM": 292.40, "IEF": 90.71, "GLDM": 82.60}


def test_budget_leaves_room_for_fx_and_fee() -> None:
    usd = m.budget_usd(1_000_000, 1_400)
    assert usd == pytest.approx(1_000_000 / (1_400 * 1.01) / 1.0009)
    assert m.budget_usd(1_000_000, 0) == 0.0


def test_limit_price_rounds_up_to_cent() -> None:
    assert m.limit_price(89.68) == 89.86  # 89.68 × 1.002 = 89.859 → 올림
    assert m.limit_price(100.0) == 100.2


def test_first_round_close_to_weights_and_within_budget() -> None:
    usd = m.budget_usd(1_000_000, 1_400)
    plan = m.plan_buys({}, ASKS, usd)
    spend = {s: n * m.limit_price(ASKS[s]) for s, n in plan.items()}
    assert sum(spend.values()) <= usd
    assert usd - sum(spend.values()) < min(m.limit_price(a) for a in ASKS.values())
    for s, w in m.WEIGHTS.items():
        assert spend[s] / usd == pytest.approx(w, abs=0.15)


def test_buy_only_fills_the_short_side_first() -> None:
    held = {"SPYM": 10.0, "QQQM": 0.0, "IEF": 3.0, "GLDM": 3.0}  # S&P 쪽이 많음
    plan = m.plan_buys(held, ASKS, 600.0)
    assert "SPYM" not in plan and plan.get("QQQM", 0) >= 1
    assert all(n > 0 for n in plan.values())
    assert sum(n * m.limit_price(ASKS[s]) for s, n in plan.items()) <= 600.0


def test_missing_price_means_no_plan() -> None:
    assert m.plan_buys({}, {"SPYM": 90.0}, 700.0) == {}


def test_state_round_trip_rounds_and_fills() -> None:
    st = m.PolarisState()
    assert st.due_month("2026-10") is False  # 11 월부터
    assert st.due_month("2026-11")
    st.rounds.append(m.Round("2026-11", 1_000_000, nonce="abc"))
    assert not st.due_month("2026-11")
    st.apply_fill("SPYM", 3, 269.0, 0.24, 1_400, "20261103:1")
    st.rounds[0].status = "done"
    back = m.PolarisState.from_dict(st.to_dict())
    assert back.qty["SPYM"] == 3 and back.cost_usd["SPYM"] == pytest.approx(269.24)
    assert back.cost_krw["SPYM"] == pytest.approx(269.24 * 1_400)
    assert back.done_rounds == 1 and back.rounds[0].nonce == "abc"
    skipped = m.PolarisState(rounds=[m.Round("2026-12", 1, status="skipped")])
    assert skipped.done_rounds == 0 and not skipped.due_month("2026-12")
    assert skipped.due_month("2027-01")
    full = m.PolarisState(rounds=[m.Round(f"2027-0{k}", 1, status="done") for k in range(1, 5)])
    assert not full.due_month("2027-05")


def test_card_and_result_text() -> None:
    st = m.PolarisState()
    plan = m.plan_buys({}, ASKS, m.budget_usd(1_000_000, 1_400))
    card = m.card_text(st, "2026-11", 1_000_000, plan, ASKS, 1_400)
    assert "Polaris 1/4회차 · 11월" in card and "100.0만 원" in card
    assert "SPYM" in card and "지금 Polaris: 없음" in card and "자동 매도 없음" in card
    st.apply_fill("SPYM", 3, 269.0, 0.24, 1_400, "x")
    out = m.result_text(
        "2026-11", {"SPYM": (3, 269.0)}, {"QQQM": 1}, 269.24, 1_400, st, {"SPYM": 95.0}, final=True
    )
    assert "SPYM 3주 · 평균 $89.67" in out and "QQQM 1주 못 삼" in out
    assert "여기까지" in out and "평단 $89.75 → $95.00" in out
