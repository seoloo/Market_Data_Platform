"""나무 모의 따라가기 — 무엇을 몇 주 (가짜 Paper · 호가)."""

from __future__ import annotations

from auto_trading.ops.namu_follow import FEE, START_KRW, Ledger, plan


def _px(**kw: int) -> dict[str, int]:
    return dict(kw)


def test_decision_day_buys_new_names_at_ten_percent_each() -> None:
    paper = {"positions": [], "sell_next_close": {}, "buy_next_close": ["A", "B"]}
    orders = plan(paper, {}, _px(A=10_000, B=50_000), _px(A=9_990, B=49_950), Ledger())
    assert [(o.side, o.code, o.qty, o.price) for o in orders] == [
        ("buy", "A", 70, 10_000),  # 700 만 × 10% = 70 만 → 70 주
        ("buy", "B", 14, 50_000),
    ]


def test_index_below_200d_means_no_orders() -> None:
    paper: dict[str, object] = {"positions": [], "sell_next_close": {}, "buy_next_close": None}
    assert plan(paper, {}, {}, {}, Ledger()) == []


def test_sells_paper_exits_and_names_paper_no_longer_holds() -> None:
    paper = {
        "positions": [{"code": "A", "weight": 0.1}, {"code": "B", "weight": 0.1}],
        "sell_next_close": {"A": "trend_off"},
        "buy_next_close": None,
    }
    held = {"A": 70, "B": 14, "Z": 5}
    orders = plan(paper, held, _px(A=1, B=1, Z=1), _px(A=10_000, B=50_000, Z=2_000), Ledger(0))
    assert [(o.side, o.code, o.qty, o.why) for o in orders] == [
        ("sell", "A", 70, "Paper 매도"),
        ("sell", "Z", 5, "Paper 에 없음"),
    ]


def test_missed_buy_is_caught_up_in_proportion_to_ledger() -> None:
    paper = {"positions": [{"code": "A", "weight": 0.2}], "buy_next_close": None}
    got = plan(paper, {}, _px(A=10_000), _px(A=9_990), Ledger())
    assert [(o.code, o.qty, o.why) for o in got] == [("A", 140, "Paper 보유 맞추기")]  # 20%
    poor = plan(paper, {}, _px(A=10_000), _px(A=9_990), Ledger(cash=50_000))
    assert [o.qty for o in poor] == [1]  # 장부 5 만 원의 20% = 1 만 원 → 1 주


def test_ledger_charges_paper_cost_both_ways() -> None:
    led = Ledger()
    led.fill("2026-10-16", "buy", "A", 10, 10_000)
    led.fill("2026-10-17", "sell", "A", 10, 10_000)
    assert round(led.cash) == round(START_KRW - 100_000 * FEE * 2)
    assert Ledger.from_json(led.to_json()).fills == led.fills
