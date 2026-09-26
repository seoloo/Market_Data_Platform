"""QNDX 적립 알림 (문서 89) — 적립·매수 확인·한도·화면. 주문 경로 없음."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.exchange.kis import BuyingPower, ReservationResult
from auto_trading.ops.qndx_alert import (
    FEE,
    Order,
    State,
    advise,
    message,
    reconcile,
    resolve_orders,
    should_order,
)
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
    assert a.limit_usd == 25.75  # 현재가 +3%


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


# ---------------------------------------------------------------- 자동 예약 매수 판단 (문서 89)


def _buy_advice(s: State, today: str = "2026-09-29", weekday: int = 1):
    return advise(s, today, PRICE, FX, weekday=weekday)


def test_should_order_gates() -> None:
    s = _state()
    a = _buy_advice(s)
    assert a.status == "BUY"
    assert should_order(s, a, trading_enabled=True, halted=None) == (True, "예약 매수 1주")
    assert not should_order(s, a, trading_enabled=False, halted=None)[0]
    assert not should_order(s, a, trading_enabled=True, halted="점검")[0]
    assert not should_order(s, _buy_advice(s, "2026-10-03", 5), trading_enabled=True, halted=None)[
        0
    ]
    s.orders.append(Order("2026-09-29", 25.75, "REJECTED"))
    assert should_order(s, a, trading_enabled=True, halted=None)[1] == "오늘은 이미 주문했다"
    wait = advise(_state(), "2026-09-26", PRICE, FX, weekday=5)
    assert not should_order(_state(), wait, trading_enabled=True, halted=None)[0]


def test_resolve_orders_fill_or_expire() -> None:
    s = _state()
    s.orders.append(Order("2026-09-29", 25.75, "SENT"))
    assert resolve_orders(s, "2026-09-30", bought_now=True) == []
    assert s.orders[0].status == "FILLED"
    s.orders.append(Order("2026-09-30", 25.75, "UNKNOWN"))
    notes = resolve_orders(s, "2026-10-01", bought_now=False)
    assert s.orders[1].status == "EXPIRED" and "만료" in notes[0]
    s.orders.append(Order("2026-10-01", 25.75, "SENT"))
    resolve_orders(s, "2026-10-01", bought_now=False)  # 오늘 낸 주문은 아직 그대로
    assert s.orders[2].status == "SENT"


class _FakeBroker:
    def __init__(self, *, max_qty: str = "3", outcome: str = "ok") -> None:
        self.max_qty, self.outcome, self.sent = max_qty, outcome, 0

    def buying_power(self, symbol: str, exch: str, price: Decimal) -> BuyingPower:
        return BuyingPower(Decimal(self.max_qty), Decimal("1400"), Decimal("80"))

    def reserve_buy(self, symbol: str, exch: str, qty: int, limit: Decimal) -> ReservationResult:
        self.sent += 1
        if self.outcome == "unknown":
            raise OrderStateUnknownError("timeout")
        return ReservationResult(self.outcome == "ok", "C1", "msg", {})


def _script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import scripts.qndx_alert as mod

    monkeypatch.setattr(mod, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "HALT", tmp_path / "HALT")
    return mod


@pytest.mark.parametrize(
    ("outcome", "status", "word"),
    [("ok", "SENT", "접수"), ("reject", "REJECTED", "거절"), ("unknown", "UNKNOWN", "결과 모름")],
)
def test_script_order_is_written_before_sending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str, status: str, word: str
) -> None:
    mod = _script(monkeypatch, tmp_path)
    s, broker = _state(), _FakeBroker(outcome=outcome)
    line = mod._maybe_order(broker, s, _buy_advice(s), True)
    assert broker.sent == 1 and word in line
    assert s.orders[-1].status == status and s.orders[-1].limit_usd == 25.75
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["orders"][-1]["date"] == "2026-09-29"  # 보내기 전에 기록됨
    again = mod._maybe_order(broker, s, _buy_advice(s), True)  # 같은 날 두 번째 실행
    assert broker.sent == 1 and "이미 주문" in again


def test_script_skips_without_money_or_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _script(monkeypatch, tmp_path)
    s, broker = _state(), _FakeBroker(max_qty="0")
    assert "입금 필요" in mod._maybe_order(broker, s, _buy_advice(s), True)
    assert broker.sent == 0 and s.orders == []
    assert mod._maybe_order(broker, s, _buy_advice(s), False) is None  # 꺼짐 → 앱 안내
    (tmp_path / "HALT").write_text("점검", encoding="utf-8")
    assert "긴급 정지" in mod._maybe_order(_FakeBroker(), s, _buy_advice(s), True)
