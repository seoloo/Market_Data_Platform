"""적립 매수 (문서 89) — 종목별 계획(QNDX 원화 · BTQ 달러), 매수 확인, 한도, 자동 매수, 화면."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.exchange.kis import BuyingPower, ReservationResult
from auto_trading.ops.qndx_alert import (
    FEE,
    PLANS,
    Order,
    Plan,
    State,
    advise,
    message,
    reconcile,
    resolve_orders,
    should_order,
)
from auto_trading.ui.qndx_render import accumulate_page

QNDX, BTQ = PLANS
PRICE, FX = 25.0, 1_400.0  # 1주 35,000원 (+수수료 87.5원)


def _state(symbol: str = "QNDX") -> State:
    return State(symbol, "2026-09-26", 0.0, 0.0, 0.0)


def test_plans_are_what_the_user_asked() -> None:
    assert (QNDX.symbol, QNDX.currency, QNDX.daily, QNDX.monthly_cap) == (
        "QNDX",
        "KRW",
        10_000.0,
        300_000.0,
    )
    assert (BTQ.symbol, BTQ.currency, BTQ.daily) == ("BTQ", "USD", 1.0)


def test_qndx_buy_on_day_four_like_the_user_example() -> None:
    s = _state()
    days = ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29"]
    status = [advise(s, QNDX, d, PRICE, FX, weekday=0).status for d in days]
    assert status == ["WAIT", "WAIT", "WAIT", "BUY"]
    a = advise(s, QNDX, "2026-09-26", PRICE, FX, weekday=0)
    assert a.days_to_go == 3 and abs(a.price - PRICE * FX * (1 + FEE)) < 1e-6
    assert a.limit_usd == 25.75  # 현재가 +3%


def test_btq_accrues_one_dollar_a_day_without_fx() -> None:
    s = _state("BTQ")
    status = [
        advise(s, BTQ, d, 2.74, FX, weekday=1).status
        for d in ("2026-09-26", "2026-09-27", "2026-09-28")
    ]
    assert status == ["WAIT", "WAIT", "BUY"]  # 3 달러 ≥ 2.74 × 1.0025
    reconcile(s, BTQ, "2026-09-29", 1.0, 2.75, FX)
    a = advise(s, BTQ, "2026-09-29", 2.74, FX, weekday=2)
    assert abs(a.accrued - (4.0 - 2.75 * (1 + FEE))) < 1e-9  # 환율 곱하지 않는다
    assert "$" in message(a, [])


def test_manual_buy_is_found_in_balance_and_leftover_carries() -> None:
    s = _state()
    notes = reconcile(s, QNDX, "2026-09-29", 1.0, 25.1, FX)
    assert len(s.buys) == 1 and "1주 매수 확인" in notes[0]
    a = advise(s, QNDX, "2026-09-29", PRICE, FX, weekday=1)
    assert abs(a.accrued - (40_000 - 25.1 * FX * (1 + FEE))) < 1e-6
    assert a.status == "WAIT" and a.bought_qty == 1
    assert reconcile(s, QNDX, "2026-09-30", 1.0, 25.1, FX) == []  # 같은 잔고 → 다시 빼지 않음
    sold = reconcile(s, QNDX, "2026-10-01", 0.0, 0.0, FX)
    assert "줄었다" in sold[0] and len(s.buys) == 1


def test_existing_holding_and_old_state_file() -> None:
    s = State("QNDX", "2026-09-26", 3.0, 3.0, 3 * 20.0)
    assert reconcile(s, QNDX, "2026-09-26", 3.0, 20.0, FX) == []
    assert advise(s, QNDX, "2026-09-26", PRICE, FX, weekday=5).accrued == 10_000
    old = {
        "symbol": "QNDX",
        "start_date": "2026-09-26",
        "baseline_qty": 0.0,
        "last_qty": 1.0,
        "last_cost_usd": 25.0,
        "buys": [
            {"date": "2026-09-29", "qty": 1, "cost_usd": 25, "fx_rate": FX, "cost_krw": 35000}
        ],
    }
    assert State.from_dict(old).buys[0].cost == 35000  # 첫 판 이름(cost_krw) 읽기


def test_monthly_cap_and_weekend_note() -> None:
    s = _state()
    for k in range(8):  # 이번 달 8주 ≈ 281,000원 → 9번째는 한도(300,000) 초과
        reconcile(s, QNDX, "2026-10-20", float(k + 1), 25.0, FX)
    assert advise(s, QNDX, "2026-10-31", PRICE, FX, weekday=5).status == "CAP"
    buy = advise(_state(), QNDX, "2026-10-03", PRICE, FX, weekday=5)
    assert buy.status == "BUY" and buy.weekend
    assert "월요일 밤" in message(buy, [])


# ---------------------------------------------------------------- 자동 예약 매수


def _buy_advice(s: State, today: str = "2026-09-29", weekday: int = 1) -> Any:
    return advise(s, QNDX, today, PRICE, FX, weekday=weekday)


def test_should_order_gates() -> None:
    s = _state()
    a = _buy_advice(s)
    assert a.status == "BUY"
    assert should_order(s, a, trading_enabled=True, halted=None) == (True, "예약 매수 1주")
    assert not should_order(s, a, trading_enabled=False, halted=None)[0]
    assert not should_order(s, a, trading_enabled=True, halted="점검")[0]
    weekend = _buy_advice(s, "2026-10-03", 5)
    assert not should_order(s, weekend, trading_enabled=True, halted=None)[0]
    s.orders.append(Order("2026-09-29", 25.75, "REJECTED"))
    assert should_order(s, a, trading_enabled=True, halted=None)[1] == "오늘은 이미 주문했다"


def test_only_confirmed_qndx_etp_rejection_can_retry_once() -> None:
    s = _state()
    a = _buy_advice(s)
    s.orders.append(Order(a.today, 25.75, "REJECTED", "APBK1672"))
    assert should_order(s, a, trading_enabled=True, halted=None)[0] is False
    assert should_order(
        s, a, trading_enabled=True, halted=None, retry_confirmed_etp_rejection=True
    )[0]
    s.orders.append(Order(a.today, 25.75, "UNKNOWN"))
    assert not should_order(
        s, a, trading_enabled=True, halted=None, retry_confirmed_etp_rejection=True
    )[0]
    s.orders.pop()
    s.orders[0].code = "OTHER"
    assert not should_order(
        s, a, trading_enabled=True, halted=None, retry_confirmed_etp_rejection=True
    )[0]
    s.orders[0].code = "APBK1672"
    assert not should_order(
        s, a, trading_enabled=False, halted=None, retry_confirmed_etp_rejection=True
    )[0]
    assert not should_order(
        s, a, trading_enabled=True, halted="stop", retry_confirmed_etp_rejection=True
    )[0]
    s.symbol = "BTQ"
    assert not should_order(
        s, a, trading_enabled=True, halted=None, retry_confirmed_etp_rejection=True
    )[0]


def test_resolve_orders_fill_or_expire() -> None:
    s = _state()
    s.orders.append(Order("2026-09-29", 25.75, "SENT"))
    assert resolve_orders(s, "2026-09-30", bought_now=True) == []
    assert s.orders[0].status == "FILLED"
    s.orders.append(Order("2026-09-30", 25.75, "UNKNOWN"))
    notes = resolve_orders(s, "2026-10-01", bought_now=False)
    assert s.orders[1].status == "EXPIRED" and "만료" in notes[0]
    s.orders.append(Order("2026-10-01", 25.75, "SENT"))
    resolve_orders(s, "2026-10-01", bought_now=False)  # 오늘 낸 주문은 그대로
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


def _script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    import scripts.qndx_alert as mod

    monkeypatch.setattr(mod, "DATA", tmp_path)
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
    line = mod._maybe_order(broker, QNDX, s, _buy_advice(s), True)
    assert broker.sent == 1 and word in line
    assert s.orders[-1].status == status and s.orders[-1].limit_usd == 25.75
    saved = json.loads((tmp_path / "QNDX.json").read_text(encoding="utf-8"))
    assert saved["orders"][-1]["date"] == "2026-09-29"  # 보내기 전에 기록됨
    again = mod._maybe_order(broker, QNDX, s, _buy_advice(s), True)  # 같은 날 두 번째 실행
    assert broker.sent == 1 and "이미 주문" in again


def test_script_etp_retry_sends_once_then_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _script(monkeypatch, tmp_path)
    s, broker = _state(), _FakeBroker()
    s.orders.append(Order("2026-09-29", 25.75, "REJECTED", "APBK1672"))
    a = _buy_advice(s)
    line = mod._maybe_order(broker, QNDX, s, a, True, retry_confirmed_etp_rejection=True)
    assert line is not None and "접수" in line and broker.sent == 1
    assert [o.status for o in s.orders] == ["REJECTED", "SENT"]
    second = mod._maybe_order(broker, QNDX, s, a, True, retry_confirmed_etp_rejection=True)
    assert second is not None and "이미 주문" in second and broker.sent == 1


def test_script_skips_without_money_disabled_halt_or_over_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _script(monkeypatch, tmp_path)
    s, broker = _state(), _FakeBroker(max_qty="0")
    assert "통합증거금" in mod._maybe_order(broker, QNDX, s, _buy_advice(s), True)
    assert broker.sent == 0 and s.orders == []
    assert mod._maybe_order(broker, QNDX, s, _buy_advice(s), False) is None  # 꺼짐 → 앱 안내
    cheap = Plan("QNDX", "NAS", "NASD", "KRW", 10_000.0, 300_000.0, 20.0)
    assert "상한" in mod._maybe_order(_FakeBroker(), cheap, s, _buy_advice(s), True)
    (tmp_path / "HALT").write_text("점검", encoding="utf-8")
    assert "긴급 정지" in mod._maybe_order(_FakeBroker(), QNDX, s, _buy_advice(s), True)


def test_page_renders_each_plan(tmp_path: Path) -> None:
    for plan, price in ((QNDX, PRICE), (BTQ, 2.74)):
        s = _state(plan.symbol)
        a = advise(s, plan, "2026-09-29", price, FX, weekday=0)
        folder = tmp_path / "reports" / "accumulate" / plan.symbol
        folder.mkdir(parents=True)
        (folder / "2026-09-29.json").write_text(
            json.dumps(
                {
                    "date": "2026-09-29",
                    "at_ms": int(datetime(2026, 9, 29, 12, tzinfo=UTC).timestamp() * 1000),
                    "status": a.status,
                    "advice": a.to_dict(),
                    "holding_qty": 2 if plan.symbol == "QNDX" else 0,
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "data" / "accumulate").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / "accumulate" / f"{plan.symbol}.json").write_text(
            json.dumps(s.to_dict()), encoding="utf-8"
        )
    html = accumulate_page(SimpleNamespace(root=tmp_path))
    assert "QNDX · 1주 살 차례" in html and "40,000원" in html
    assert "BTQ" in html and "$4.00" in html
    assert "QNDX" in html and "2주" in html
    assert "2026-09-29 21:00 KST" in html
    assert 'id="qndx"' in html and 'id="btq"' in html
    assert "아직 기록이 없다" in accumulate_page(SimpleNamespace(root=tmp_path / "none"))


def test_overseas_report_date_is_escaped_when_timestamp_invalid(tmp_path: Path) -> None:
    folder = tmp_path / "reports" / "accumulate" / "QNDX"
    folder.mkdir(parents=True)
    (folder / "2026-09-29.json").write_text(
        json.dumps({"date": "<script>bad</script>", "at_ms": "invalid", "status": "WAIT"}),
        encoding="utf-8",
    )
    html = accumulate_page(SimpleNamespace(root=tmp_path))
    assert "<script>" not in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html


def test_run_retries_failed_plans_then_notifies_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import scripts.qndx_alert as mod

    monkeypatch.setattr(mod, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(mod, "DATA", tmp_path / "data")
    monkeypatch.setattr(mod, "_migrate", lambda: None)
    sent: list[bool] = []
    monkeypatch.setattr(mod, "_notify", lambda text, send: sent.append(send))
    tries: list[list[str]] = []

    def attempt(pending: list[Plan], now: Any, results: dict[str, str]) -> dict[str, str]:
        tries.append([p.symbol for p in pending])
        if len(tries) == 1:
            return {p.symbol: "KIS 오류 500 EGW00300" for p in pending}
        for p in pending:
            results[p.symbol] = f"{p.symbol} ok"
        return {}

    monkeypatch.setattr(mod, "_attempt", attempt)
    assert mod.run(True, wait_s=0) == 0
    assert tries == [["QNDX", "BTQ"], ["QNDX", "BTQ"]] and sent == [True]

    # 실패만 알린 날: 다음 실행이 성공하면 한 번 더 알린다
    tries.clear()
    monkeypatch.setattr(mod, "_attempt", lambda pending, now, results: {"QNDX": "x", "BTQ": "x"})
    assert mod.run(True, attempts=1, wait_s=0) == 1
    monkeypatch.setattr(mod, "_attempt", attempt)
    tries.append(["done"])  # 두 번째 호출부터 성공하도록
    assert mod.run(True, wait_s=0) == 0
    assert sent[-2:] == [True, True]
