from __future__ import annotations

import math

from auto_trading.ops.theme_acc import FEE, accumulate, cohort_returns, irr

DAYS = [f"2026-11-{i:02d}" for i in range(1, 21)]


def test_monthly_money_is_split_and_never_sold() -> None:
    close = {"A": [10.0] * 20, "B": [20.0] * 10 + [40.0] * 10}
    book = accumulate(DAYS, close, {0: ["A", "B"], 9: ["A"]}, monthly=100.0, start=0)
    assert book.contributed == 200.0
    assert math.isclose(book.qty["A"], 50 * (1 - FEE) / 10 + 100 * (1 - FEE) / 10)
    assert math.isclose(book.qty["B"], 50 * (1 - FEE) / 20)
    # B 가 두 배가 돼도 팔지 않고 평가만 오른다
    assert math.isclose(book.equity[-1], book.qty["A"] * 10 + book.qty["B"] * 40)
    assert [b["names"] for b in book.baskets] == [["A", "B"], ["A"]]


def test_halted_stock_waits_then_unbought_share_stays_cash() -> None:
    halted: list[float | None] = [None] * 3 + [5.0] * 17
    never: list[float | None] = [None] * 20
    book = accumulate(DAYS, {"H": halted, "N": never}, {0: ["H", "N"]}, monthly=100.0, start=0)
    assert math.isclose(book.qty["H"], 50 * (1 - FEE) / 5)  # 3 거래일 뒤 샀다
    assert "N" not in book.qty and math.isclose(book.cash, 50.0)  # 5 거래일 넘게 못 사 현금


def test_keep_filter_carries_unspent_money_to_next_month() -> None:
    close = {"A": [10.0] * 20, "B": [10.0] * 20}
    book = accumulate(
        DAYS,
        close,
        {0: ["A", "B"], 9: ["A", "B"]},
        monthly=100.0,
        start=0,
        keep=lambda s, t: t >= 9,  # 첫 달은 아무것도 추세 위가 아님
    )
    assert book.baskets[0]["names"] == []
    assert math.isclose(book.qty["A"], 100 * (1 - FEE) / 10)  # 첫 달 돈까지 둘째 달에


def test_irr_matches_simple_growth() -> None:
    r = irr([(0, 100.0)], 252, 110.0)
    assert r is not None and math.isclose(r, 0.10, abs_tol=1e-6)
    assert irr([], 10, 1.0) is None and irr([(0, 1.0)], 10, 0.0) is None


def test_cohort_return_buys_next_day_and_marks_last_price() -> None:
    close: dict[str, list[float | None]] = {"A": [9.0, 10.0, 12.0, None], "B": [1.0, 2.0, 2.0, 3.0]}
    r = cohort_returns(close, ["A", "B", "Z"], 0, 3)
    expected = ((12 / 10) * (1 - FEE) - 1 + (3 / 2) * (1 - FEE) - 1) / 2
    assert r is not None and math.isclose(r, expected)
