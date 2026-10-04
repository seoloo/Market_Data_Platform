"""연말 양도세 공제 수확 계획 (ops/tax_harvest.py, 문서 123)."""

from __future__ import annotations

from auto_trading.ops.tax_harvest import EXEMPTION_KRW, MARGIN_KRW, Holding, plan, qs_realized_krw


def test_plan_uses_remaining_exemption_strategy_first() -> None:
    hs = [
        Holding("APH", 68, 85.0, 87.0, strategy=False),  # 주당 2 USD 이익
        Holding("QNDX", 100, 20.0, 30.0, strategy=True),  # 주당 10 USD 이익
        Holding("ETHD", 70, 30.0, 24.0, strategy=False),  # 손실 — 고르지 않음
    ]
    p = plan(hs, 1_350.0, other_gains_krw=500_000, qs_gains_krw=0, fee=0.001)
    assert p.budget_krw == EXEMPTION_KRW - 500_000 - MARGIN_KRW
    assert [(x.symbol, x.qty) for x in p.lines] == [("QNDX", 100), ("APH", 68)]
    # QNDX 100 주 전부(135 만 원) 뒤 남은 35 만 원으로 APH 주당 2,700 원 × 68 주(보유 전부)
    assert p.harvested_krw <= p.budget_krw
    assert all(x.symbol != "ETHD" for x in p.lines)
    assert abs(p.future_tax_saved_krw - p.harvested_krw * 0.22) < 1e-6


def test_no_budget_when_exemption_used() -> None:
    hs = [Holding("QNDX", 100, 20.0, 30.0, strategy=True)]
    p = plan(hs, 1_350.0, other_gains_krw=2_400_000, qs_gains_krw=0, fee=0.001)
    assert p.lines == () and p.harvested_krw == 0


def test_qs_realized_follows_average_cost_and_year() -> None:
    trades = [
        {"date": "2026-03-01", "side": "BUY", "qty": 2, "krw": 200_000},
        {"date": "2026-04-01", "side": "BUY", "qty": 2, "krw": 300_000},
        {"date": "2026-06-01", "side": "SELL", "qty": 4, "krw": 600_000},
        {"date": "2025-12-01", "side": "BUY", "qty": 1, "krw": 1},
    ]
    assert qs_realized_krw(trades[:3], "2026") == 100_000
    assert qs_realized_krw(trades[:3], "2027") == 0
