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


def test_amount_target_and_next_weekday() -> None:
    from auto_trading.ops.m3_live import amount_target, next_weekday

    assert next_weekday("2026-10-30") == "2026-11-02"  # 금 → 월
    st = M3State("2026-10-01", done_month="2026-10")
    assert amount_target(st, "2026-10-30") is None
    st.ask_month = "2026-11"
    assert amount_target(st, "2026-10-30") == "2026-11"
    assert amount_target(st, "2026-11-02") == "2026-11"  # 그 달 매수 전
    deposit(st, "2026-11-02", 500_000, 1350)
    assert amount_target(st, "2026-11-02") is None


def test_holdings_dividends_and_reason_lines() -> None:
    from auto_trading.ops.m3_live import (
        M3Order,
        M3State,
        dividend_lines,
        holdings_lines,
        share_reason,
    )

    st = M3State("2026-10-01", cash_usd=12.4)
    st.qty = {"TQQQ": 2.0, "SCHD": 3.0}
    st.orders = [
        M3Order(
            "2026-10-01", "TQQQ", "BUY", 2, 80, "a", filled_qty=2, filled_usd=160, commission=0.2
        ),
        M3Order(
            "2026-10-01", "SCHD", "BUY", 3, 33, "b", filled_qty=3, filled_usd=99, commission=0.1
        ),
    ]
    text = "\n".join(holdings_lines(st, {"TQQQ": 90.0, "SCHD": 32.0}))
    assert "🔺 TQQQ 2주 · $180.00 (+12.4%)" in text  # 180 ÷ 160.2 − 1
    assert "🔻 SCHD 3주 · $96.00 (-3.1%)" in text and "이월 현금 $12.40" in text
    st.dividends = [["2026-09-24", "SCHD", 3.0, 0.26, 0.663]]
    lines = "\n".join(dividend_lines(st, [("2026-09-24", "SCHD", 0.26)], [], {"SCHD": 6.0}))
    assert "받은 배당 누적 $0.66" in lines and "다음 예상 SCHD: 2026-12" in lines
    assert "6주 × 직전 주당 $0.2600" in lines
    assert "최대 60%" in share_reason(0.15, 0.6)
    assert "= 44% 로 줄임" in share_reason(0.27, 0.6 * 0.20 / 0.27)
