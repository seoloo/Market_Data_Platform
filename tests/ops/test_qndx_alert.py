"""QNDX 적립 알림 (문서 89) — 적립·매수 확인·한도·화면. 주문 경로 없음."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from auto_trading.ops.qndx_alert import FEE, State, advise, message, reconcile
from auto_trading.ui.qndx_render import qndx_page

PRICE, FX = 25.0, 1_400.0  # 1주 35,000원 (+수수료 87.5원)


def _state() -> State:
    return State("QNDX", "2026-09-26", 0.0, 0.0, 0.0)


def test_buy_on_day_four_like_the_user_example() -> None:
    s = _state()
    days = ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29"]
    status = [advise(s, d, PRICE, FX, weekday=0).status for d in days]
    assert status == ["WAIT", "WAIT", "WAIT", "BUY"]
    a = advise(s, "2026-09-26", PRICE, FX, weekday=0)
    assert a.days_to_go == 3 and abs(a.price_krw - PRICE * FX * (1 + FEE)) < 1e-6
    assert a.limit_usd == 25.5


def test_manual_buy_is_found_in_balance_and_leftover_carries() -> None:
    s = _state()
    notes = reconcile(s, "2026-09-29", 1.0, 25.1, FX)
    assert len(s.buys) == 1 and "1주 매수 확인" in notes[0]
    a = advise(s, "2026-09-29", PRICE, FX, weekday=1)
    spent = 25.1 * FX * (1 + FEE)
    assert abs(a.accrued_krw - (40_000 - spent)) < 1e-6
    assert a.status == "WAIT" and a.bought_qty == 1
    assert reconcile(s, "2026-09-30", 1.0, 25.1, FX) == []  # 같은 잔고 → 다시 빼지 않는다
    sold = reconcile(s, "2026-10-01", 0.0, 0.0, FX)
    assert "줄었다" in sold[0] and len(s.buys) == 1


def test_existing_holding_at_start_is_not_counted() -> None:
    s = State("QNDX", "2026-09-26", 3.0, 3.0, 3 * 20.0)
    assert reconcile(s, "2026-09-26", 3.0, 20.0, FX) == []
    assert advise(s, "2026-09-26", PRICE, FX, weekday=5).accrued_krw == 10_000


def test_monthly_cap_and_weekend_note() -> None:
    s = _state()
    for k in range(8):  # 이번 달 8주 ≈ 281,000원 → 9번째는 한도(300,000) 초과
        reconcile(s, "2026-10-20", float(k + 1), 25.0, FX)
    a = advise(s, "2026-10-31", PRICE, FX, weekday=5)
    assert a.status == "CAP"
    s2 = _state()
    buy = advise(s2, "2026-10-03", PRICE, FX, weekday=5)
    assert buy.status == "BUY" and buy.weekend
    assert "월요일 밤" in message(buy, "QNDX", [])


def test_page_renders_from_report_and_state(tmp_path: Path) -> None:
    folder = tmp_path / "reports" / "qndx_alert"
    folder.mkdir(parents=True)
    s = _state()
    a = advise(s, "2026-09-29", PRICE, FX, weekday=0)
    (folder / "2026-09-29.json").write_text(
        json.dumps(
            {"date": "2026-09-29", "status": a.status, "advice": a.to_dict(), "notified": True}
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "qndx_alert").mkdir(parents=True)
    (tmp_path / "data" / "qndx_alert" / "state.json").write_text(
        json.dumps(s.to_dict()), encoding="utf-8"
    )
    html = qndx_page(SimpleNamespace(root=tmp_path))
    assert "오늘 1주 살 차례" in html and "40,000원" in html and "보냄" in html
    empty = qndx_page(SimpleNamespace(root=tmp_path / "none"))
    assert "아직 기록이 없다" in empty
