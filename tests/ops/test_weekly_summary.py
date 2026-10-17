"""토요일 주간 요약 — 장부 체결만으로 평단 · 장부 밖 감소는 먼저 산 것부터 · 단위 가치 변화 · 글."""

from __future__ import annotations

import pytest

from auto_trading.ops import weekly_summary as m


def _o(day: str, asset: str, side: str, qty: float, quote: float) -> dict[str, object]:
    return {
        "id": f"t2b-{day}-{asset}-{side[0]}",
        "side": side,
        "status": "FILLED",
        "executed_qty": str(qty),
        "quote_qty": str(quote),
        "fee_usdt": "0",
    }


def test_avg_costs_trims_out_of_book_sales_from_oldest() -> None:
    orders = [
        _o("2026-10-15", "BTC", "BUY", 0.0003, 0.0003 * 82_000),
        _o("2026-10-16", "BTC", "BUY", 0.0012, 0.0012 * 81_600),
    ]
    got = m.avg_costs(orders, {"BTC": 0.0012})  # 10-15 몫은 장부 밖에서 팔림
    assert got["BTC"][0] == pytest.approx(0.0012)
    assert got["BTC"][1] == pytest.approx(81_600)
    both = m.avg_costs(orders, {"BTC": 0.0015})
    assert both["BTC"][1] == pytest.approx((0.0003 * 82_000 + 0.0012 * 81_600) / 0.0015)
    sold = m.avg_costs([*orders, _o("2026-10-17", "BTC", "SELL", 0.00075, 60.0)], {"BTC": 0.00075})
    assert sold["BTC"][1] == pytest.approx(both["BTC"][1])  # 매도는 평단을 바꾸지 않음
    assert m.avg_costs(orders, {"BTC": 0.0}) == {}


def test_change_since_uses_unit_value() -> None:
    pts = [("2026-10-10", 1000.0), ("2026-10-15", 1010.0)]
    pct, base = m.change_since(pts, "2026-10-11", 1030.0)
    assert pct == pytest.approx(0.03) and base == "2026-10-10"
    pct, base = m.change_since(pts, "2026-10-01", 1010.0)
    assert pct == pytest.approx(0.01) and base == "2026-10-10"


def test_summary_text_blocks_and_todo() -> None:
    vega = {
        "equity": 853.13,
        "deposits": 852.91,
        "week": -0.16,
        "week_pct": -0.0002,
        "since_pct": -0.0002,
        "dd": 0.0003,
        "holdings": [m.Holding("BTC", 0.00120255, 81_684.0, 81_584.0)],
        "cash": 618.91,
        "earn": 598.91,
        "trend": {"BTC": True, "ETH": False},
        "buys": 6,
        "sells": 0,
    }
    altair = {
        "equity": 843.38,
        "start": 851.57,
        "week": -8.19,
        "week_pct": -0.0096,
        "since_pct": -0.0096,
        "dd": 0.0096,
        "legs": [m.Leg("BTC", 0.004, 82_197.1, 81_445.0)],
        "stops": {"BTC": 65_757.6},
        "long_trades": 2,
        "short_trades": 0,
    }
    t = m.summary_text(period="10/11 ~ 10/17", vega=vega, altair=altair, todo=[])
    assert "평단 $81,684 → $81,584" in t and "−$0.12" in t and "ETH ❌" in t
    assert "BTC 롱 0.004 · 진입 $82,197 → $81,445" in t and "숏 없음" in t
    assert "합계 1,696.51 · 넣은 돈 1,704.48" in t and "✅ 할 일 없음" in t
    assert "손익 −7.97 (−0.5%) · 이번 주 −8.35" in t
    assert "노출" not in t and "청산" not in t
    bad = m.summary_text(period="x", vega=None, altair=None, todo=["Vega 조회 실패"])
    assert "Vega — 장부를 못 읽음" in bad and "⚠️ Vega 조회 실패" in bad


def test_polaris_block_is_krw_and_separate_from_binance_total() -> None:
    pol = {
        "value_krw": 1_012_345.0,
        "cost_krw": 1_000_000.0,
        "lines": ["   └ SPYM 3주 · 평단 $89.75 → $95.00 · +2.2만 원 (+5.8%)"],
        "done": 1,
        "rounds": 4,
    }
    t = m.summary_text(period="x", vega=None, altair=None, todo=[], polaris=pol)
    assert "🧭 Polaris  (나무 · 안정 적립 · 원화)" in t
    assert "평가 1,012,345원 · 산 돈 1,000,000원 · +12,345원 (+1.2%)" in t
    assert "직접 산 종목은 빼고" in t and "회차 1/4" in t and "SPYM 3주" in t
    assert "바이낸스 합계" not in t  # 바이낸스 칸이 없으면 합계 줄도 없음
