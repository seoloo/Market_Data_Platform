from __future__ import annotations

import pytest

from auto_trading.ops.deneb_live import (
    DenebOrder,
    DenebState,
    apply_fill,
    choose,
    credit_dividends,
    deposit,
    fill,
    lev_share,
    limit_price,
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
    st = DenebState("2026-10-01")
    assert deposit(st, "2026-10-09", 300_000, 1350) == pytest.approx(300_000 / 1350)
    assert deposit(st, "2026-10-12", 300_000, 1350) == 0.0
    o = DenebOrder(
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
    from auto_trading.ops.deneb_live import amount_target, next_weekday

    assert next_weekday("2026-10-30") == "2026-11-02"  # 금 → 월
    st = DenebState("2026-10-01", done_month="2026-10")
    assert amount_target(st, "2026-10-30") is None
    st.ask_month = "2026-11"
    assert amount_target(st, "2026-10-30") == "2026-11"
    assert amount_target(st, "2026-11-02") == "2026-11"  # 그 달 매수 전
    deposit(st, "2026-11-02", 500_000, 1350)
    assert amount_target(st, "2026-11-02") is None


def test_holdings_dividends_and_reason_lines() -> None:
    from auto_trading.ops.deneb_live import (
        DenebOrder,
        DenebState,
        dividend_lines,
        holdings_lines,
        share_reason,
    )

    st = DenebState("2026-10-01", cash_usd=12.4)
    st.qty = {"TQQQ": 2.0, "SCHD": 3.0}
    st.orders = [
        DenebOrder(
            "2026-10-01", "TQQQ", "BUY", 2, 80, "a", filled_qty=2, filled_usd=160, commission=0.2
        ),
        DenebOrder(
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


# ------------------------------------------------------------------ 다음 날 채우기 (B, 2026-10-18)


def test_fill_buys_most_short_symbol_one_share_at_a_time() -> None:
    last = {"TQQQ": 80.0, "SCHD": 30.0}
    # TQQQ 10 주(800) · SCHD 0, 현금 300, 몫 60% → 평가액 1100 · 목표 TQQQ 660 → SCHD 가 모자람
    plan = fill({"TQQQ": 10.0, "SCHD": 0.0}, 300.0, last, 0.6, 0.001)
    cost = limit_price(30.0, "BUY") * 1.001
    assert plan == {"SCHD": int(300.0 // cost)}  # 9 주 — 남은 현금이 1 주 값보다 적을 때까지


def test_fill_skips_unaffordable_short_symbol_and_never_sells() -> None:
    last = {"TQQQ": 80.0, "SCHD": 33.0}
    # 가장 모자란 TQQQ 1 주(+3% · 비용 82.5)는 못 사고 SCHD 1 주는 살 수 있다
    assert fill({"TQQQ": 1.0, "SCHD": 3.0}, 42.0, last, 0.6, 0.001) == {"SCHD": 1}
    assert fill({"TQQQ": 1.0, "SCHD": 3.0}, 20.0, last, 0.6, 0.001) == {}
    plan = fill({"TQQQ": 50.0, "SCHD": 0.0}, 100.0, last, 0.1, 0.001)
    assert all(n > 0 for n in plan.values())  # 넘쳐도 팔지 않는다


def test_fill_stage_two_buys_only_schd_and_respects_max_qty() -> None:
    last = {"TQQQ": 80.0, "SCHD": 30.0}
    assert set(fill({"TQQQ": 0.0, "SCHD": 0.0}, 1000.0, last, 0.0, 0.001)) == {"SCHD"}
    capped = fill({"TQQQ": 0.0, "SCHD": 0.0}, 5000.0, last, 0.0, 0.001, {"TQQQ": 60, "SCHD": 40})
    assert capped == {"SCHD": 40}
