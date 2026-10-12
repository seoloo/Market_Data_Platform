"""Paper 양도세 추정 — 공제 · 22% · 해 넘김 정산 · 12 월 공제 실현(D)."""

from __future__ import annotations

from auto_trading.ops.lt_paper import PaperState, Position
from auto_trading.ops.paper_tax import TaxBook, add_trades, estimate, harvest, roll_year


def book() -> TaxBook:
    return TaxBook(scale_krw=10_000_000, start=1_000.0)  # 공제 250 만 원 = 장부 250


def test_exemption_scales_with_notional() -> None:
    assert book().exemption == 250.0
    assert TaxBook(scale_krw=5_000_000, start=1_000.0).exemption == 500.0


def test_tax_only_above_exemption_and_settled_at_new_year() -> None:
    b = book()
    roll_year(b, "2026-10-01")
    add_trades(b, [{"gain": 200.0}, {"gain": -50.0}, {"gain": 400.0}])  # 순 550
    assert abs(estimate(b) - 0.22 * 300) < 1e-9
    row = roll_year(b, "2027-01-04")
    assert row is not None and row["year"] == "2026" and abs(row["tax"] - 66.0) < 1e-9
    assert b.realized == 0.0 and abs(b.tax_paid - 66.0) < 1e-9


def test_december_harvest_uses_remaining_exemption_once() -> None:
    b = book()
    roll_year(b, "2026-12-15")
    add_trades(b, [{"gain": 100.0}])  # 남은 공제 150
    state = PaperState(cash=0.0)
    state.positions = {
        "A": Position(1.0, 100.0, 400.0, "d0"),
        "B": Position(1.0, 100.0, 150.0, "d0"),
    }
    closes: dict[str, list[float | None]] = {"A": [400.0], "B": [150.0]}
    done = harvest(b, state, closes, 0, "2026-12-15")
    assert [x["symbol"] for x in done] == ["A"] and abs(done[0]["realized"] - 150.0) < 1e-9
    assert abs(b.realized - 250.0) < 1e-9 and estimate(b) == 0.0
    assert abs(state.positions["A"].cost - 250.0) < 1e-9
    assert harvest(b, state, closes, 0, "2026-12-16") == []  # 그해 한 번만
    assert harvest(book(), state, closes, 0, "2026-12-14") == []  # 15 일 전엔 안 함
