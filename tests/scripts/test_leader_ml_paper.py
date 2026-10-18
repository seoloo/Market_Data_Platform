"""문서 277 — 주도주 GBM Paper 장부 (가짜 일봉 · 가짜 결정, 모델 없음)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts import leader_ml_paper as lp


def _days() -> list[str]:
    out = [f"2026-10-{d:02d}" for d in range(26, 31)]
    out += [f"2026-11-{d:02d}" for d in range(2, 31)]
    while len(out) < 140:
        out.append(f"2027-{(len(out) // 28) % 12 + 1:02d}-{len(out) % 28 + 1:02d}")
    return sorted(set(out))[:140]


def test_buy_whole_shares_with_fee_and_skip() -> None:
    book: dict[str, Any] = {"cash": 6000.0, "lots": [], "closed": [], "skipped": [], "equity": {}}
    lp.buy(book, ["A", "B"], {"A": 100.0, "B": 600.0}, "d", equity=6000.0)
    # 몫 1,000 → 종목당 500: A 4 주(100 × 1.0025 × 4 = 401), B 0 주 → 건너뜀
    assert [(x["sym"], x["qty"]) for x in book["lots"]] == [("A", 4)]
    assert book["skipped"][0]["sym"] == "B"
    assert book["cash"] == pytest.approx(6000 - 4 * 100 * 1.0025)


def test_process_decides_at_month_change_fills_next_day_and_sells_after_hold() -> None:
    days = _days()
    close: dict[str, list[float | None]] = {
        "A": [100.0] * len(days),
        "B": [50.0] * len(days),
    }
    spy = [400.0] * len(days)
    calls: list[str] = []

    def decide(t: int) -> lp.Picks:
        calls.append(days[t])
        return {"GBM": ["A", "B"], "BASE": ["B"]}

    st = lp.new_state("2026-10-01")
    lp.process(st, days, close, spy, decide)
    assert calls[0] == "2026-10-30"  # 10 월 마지막 거래일 자료로 결정
    first = st["decisions"]["2026-10-30"]
    assert first["fill_day"] == "2026-11-02"
    closed = st["books"]["GBM10"]["closed"]
    assert [c["sym"] for c in closed[:2]] == ["A", "B"]
    # 126 거래일 뒤 같은 가격에 팔면 수익 = 수수료 두 번만큼 손해
    assert closed[0]["ret"] == pytest.approx(0.9975 / 1.0025 - 1)
    assert days.index(closed[0]["sell_day"]) - days.index(closed[0]["buy_day"]) == lp.HOLD
    assert [x["sym"] for x in st["books"]["BASE10"]["closed"][:1]] == ["B"]
    assert st["spy"]["qty"] == int(lp.SPY_CAPITAL // (400 * 1.0025))
    # 다시 돌려도 이미 처리한 날은 건너뜀
    n = len(calls)
    lp.process(st, days, close, spy, decide)
    assert len(calls) == n


def test_summary_reports_books() -> None:
    days = _days()
    close: dict[str, list[float | None]] = {"A": [100.0] * len(days)}
    st = lp.new_state("2026-10-01")
    lp.process(st, days, close, [400.0] * len(days), lambda t: {"GBM": ["A"], "BASE": ["A"]})
    rep = lp.summary(st, close, days)
    assert set(lp.BOOKS) <= set(rep) and "SPY" in rep
    assert "GBM10" in lp.text(rep)
