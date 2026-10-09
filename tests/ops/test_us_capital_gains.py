from __future__ import annotations

from auto_trading.ops.us_capital_gains import (
    Opening,
    Trade,
    build,
    by_tag,
    fx_on,
    unrealized,
    us_settle,
    year_summary,
)

RATES = {"2026-01-05": 1300.0, "2026-03-03": 1400.0, "2026-06-01": 1350.0}


def test_us_settle_is_new_york_trade_date_plus_one_settlement_day() -> None:
    # KST 10-08 05:00 = 뉴욕 10-07 16:00 → 10-08
    assert us_settle("2026-10-08T05:00:02.332+09:00") == "2026-10-08"
    # 금요일 체결 → 월요일, 10-12(콜럼버스) 는 결제 휴일 → 10-13
    assert us_settle("2026-10-10T05:00:00+09:00") == "2026-10-13"
    # 12-30(수) 체결 → 12-31, 12-31(목) 체결 → 다음 해 01-04
    assert us_settle("2026-12-31T06:00:00+09:00") == "2026-12-31"
    assert us_settle("2027-01-01T06:00:00+09:00") == "2027-01-04"


def test_fx_on_uses_previous_business_day() -> None:
    assert fx_on(RATES, "2026-03-04") == 1400.0
    assert fx_on(RATES, "2026-01-01") == 1300.0  # 앞 값이 없으면 가장 이른 값


def test_moving_average_vs_fifo_and_fx_on_settlement_dates() -> None:
    trades = [
        Trade("AAA", "BUY", 1, 100.0, 0.0, "2026-01-05", "2026-01-02T00", "M3"),
        Trade("AAA", "BUY", 1, 200.0, 0.0, "2026-03-03", "2026-03-02T00", "M3"),
        Trade("AAA", "SELL", 1, 300.0, 1.0, "2026-06-01", "2026-05-29T00", "M3"),
    ]
    book = build(trades, RATES)
    # 이동평균 원가 = (100×1300 + 200×1400)/2 = 205,000 / 매도 (300 − 1)×1350 = 403,650
    assert round(book.realized[0].gain_krw) == 403_650 - 205_000
    # 선입선출 원가 = 첫 주 130,000
    assert round(book.realized_fifo[0].gain_krw) == 403_650 - 130_000
    s = year_summary(book, "2026")
    assert s["deduction_left_krw"] == 2_500_000 - (403_650 - 205_000) and s["tax_krw"] == 0
    assert by_tag(book, "2026") == {"M3": 403_650 - 205_000}
    u = unrealized(book, {"AAA": 250.0}, 1400.0)
    assert round(u["AAA"]["gain_krw"]) == 350_000 - 205_000


def test_losses_offset_tax_and_openings_are_flagged() -> None:
    trades = [
        Trade("WIN", "BUY", 10, 1000.0, 0.0, "2026-01-05", "a"),
        Trade("WIN", "SELL", 10, 4000.0, 0.0, "2026-06-01", "b"),  # 5,400,000 − 1,300,000
        Trade("OLD", "SELL", 5, 100.0, 0.0, "2026-06-01", "c"),
    ]
    book = build(trades, RATES, [Opening("OLD", 5, 40.0, "2026-01-05")])  # 원가 260,000
    s = year_summary(book, "2026")
    assert round(s["gain_krw"]) == 4_100_000 + (135_000 - 260_000)
    assert round(s["tax_krw"]) == round((3_975_000 - 2_500_000) * 0.22)
    loss = year_summary(
        build([*trades[2:]], RATES, [Opening("OLD", 5, 40.0, "2026-01-05")]), "2026"
    )
    assert loss["deduction_left_krw"] == 2_500_000 and loss["tax_free_room_krw"] == 2_625_000
    assert any(r.symbol == "OLD" and r.estimated for r in book.realized)
    over = build([Trade("X", "SELL", 1, 10.0, 0.0, "2026-06-01", "d")], RATES)
    assert over.unmatched == ["X 2026-06-01 1주 원가 모름"]
