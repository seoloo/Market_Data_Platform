from __future__ import annotations

import pytest

from auto_trading.ops.m3_live import (
    M3Order,
    M3State,
    apply_fill,
    choose,
    credit_dividends,
    deposit,
    lev_share,
)


def test_choose_today_example() -> None:
    plan = choose(
        {"TQQQ": 0.0, "SCHD": 0.0}, 300_000 / 1343.4, {"TQQQ": 80.23, "SCHD": 33.15}, 0.6, 0.001
    )
    assert plan == {"TQQQ": 1, "SCHD": 3}


def test_choose_sells_only_when_over_by_more_than_a_share() -> None:
    last = {"TQQQ": 100.0, "SCHD": 30.0}
    # TQQQ 10 주(1000) · SCHD 0, 현금 0, 몫 30% → 목표 TQQQ 300 → 7 주 넘게 많음 → 팔기
    plan = choose({"TQQQ": 10.0, "SCHD": 0.0}, 0.0, last, 0.3, 0.001)
    assert plan["TQQQ"] < 0
    # 조금만 많으면 팔지 않는다
    assert "TQQQ" not in choose({"TQQQ": 7.0, "SCHD": 10.0}, 0.0, last, 0.6, 0.001)


def test_deposit_once_per_month_and_fill_and_dividend() -> None:
    st = M3State("2026-10-01")
    assert deposit(st, "2026-10-09", 300_000, 1350) == pytest.approx(300_000 / 1350)
    assert deposit(st, "2026-10-12", 300_000, 1350) == 0.0
    o = M3Order(
        "2026-10-09",
        "SCHD",
        "BUY",
        3,
        34.0,
        "c",
        status="FILLED",
        filled_qty=3,
        filled_usd=99.0,
        commission=0.1,
    )
    st.orders.append(o)
    apply_fill(st, o)
    assert st.qty["SCHD"] == 3
    notes = credit_dividends(st, [("2026-12-10", "SCHD", 0.25)], "2026-12-25", lambda a, b: 15)
    assert notes and st.dividends[0][4] == pytest.approx(3 * 0.25 * 0.85)
    assert credit_dividends(st, [("2026-12-10", "SCHD", 0.25)], "2026-12-26", lambda a, b: 16) == []


def test_lev_share_uses_previous_month() -> None:
    closes = [(f"2026-09-{d:02d}", 100.0) for d in range(1, 30)] + [("2026-10-01", 50.0)]
    vol, share = lev_share(closes, "2026-10-09")
    assert vol == pytest.approx(0.0) and share == pytest.approx(0.6)
