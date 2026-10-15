"""텔레그램 '개별주' — 추천 + [이대로 매수] 버튼(5 분 · 한 번) → 예약. 가짜 봇 · 가짜 size_guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_trading.notify.inbox import InboxMessage, is_command, is_stock
from auto_trading.ops import user_settings
from auto_trading.ops.liquidate import Store
from auto_trading.ops.position_size import Advice
from auto_trading.ops.size_order import Ticket
from scripts import size_guard as sg
from scripts import telegram_bot as tb
from tests.ops.test_telegram_liquidation import FakeBot


def _advice(ok: bool = True) -> Advice:
    return Advice(
        symbol="NVDA",
        name="엔비디아",
        currency="USD",
        price=200.0,
        base_krw=10_000_000.0,
        weight=0.25,
        limit=201.0,
        qty=8,
        amount_krw=2_500_000.0,
        stop=194.97,
        risk_krw=70_000.0,
        lines=["✅ 추세 좋음"],
        blocked=[] if ok else ["추세 아님 — 50일선 아래 (-2.9%)"],
    )


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"reserve": [], "release": [], "advise": []}
    monkeypatch.setattr(tb, "STOCK_STORE", Store(tmp_path / "tg"))
    monkeypatch.setattr(user_settings, "get", lambda root, key: True)

    def advise(q: str) -> Advice:
        calls["advise"].append(q)
        return _advice(ok=q != "BAD")

    def reserve(q: str) -> str:
        calls["reserve"].append(q)
        return "⏳ 예약했어요"

    def release(tid: str, allowed: tuple[str, ...], status: str) -> str:
        calls["release"].append((tid, status))
        return "엔비디아 → released"

    monkeypatch.setattr(sg, "advise", advise)
    monkeypatch.setattr(sg, "reserve", reserve)
    monkeypatch.setattr(sg, "_set_status", release)
    monkeypatch.setattr(sg, "load_tickets", lambda: [])
    return calls


def _m(text: str = "", data: str = "") -> InboxMessage:
    return InboxMessage(1, 0, text, data=data, callback_id="cb" if data else "", message_id=7)


def test_routing_needs_prefix() -> None:
    for t in ("개별주 NVDA", "개별주NVDA", "/stock nvda", "개별주 삼성전자", "개별주", "비중 TSLA"):
        assert is_stock(_m(t)) and is_command(_m(t)), t
    for t in ("NVDA", "30만", "현황", "개별주식이 뭐야"):
        assert not is_stock(_m(t)), t
    assert is_stock(_m(data="stk:ab12:NVDA")) and not is_stock(_m(data="liq:ab12:all"))
    assert tb._stock_query("개별주 삼성 전자") == "삼성 전자"
    assert tb._stock_query("/stock NVDA") == "NVDA" and tb._stock_query("개별주") == ""


def test_buy_needs_button_and_runs_once(fake: dict[str, list[Any]]) -> None:
    bot = FakeBot()
    assert tb.handle_stock(bot, _m("개별주 NVDA"))  # type: ignore[arg-type]
    kind, (text, buttons) = bot.out[-1]
    assert kind == "buttons" and "매수 비중 추천" in text and not fake["reserve"]
    buy = buttons[0][0]
    assert buy["text"] == "🛒 이대로 매수 (8주)" and buy["callback_data"].endswith(":NVDA")
    assert not tb.handle_stock(bot, _m(data=buy["callback_data"]))  # type: ignore[arg-type]
    assert fake["reserve"] == ["NVDA"] and bot.out[-1] == ("send", "⏳ 예약했어요")
    tb.handle_stock(bot, _m(data=buy["callback_data"]))  # type: ignore[arg-type]  # 두 번째
    assert fake["reserve"] == ["NVDA"] and "지난 버튼" in bot.out[-1][1]


def test_blocked_advice_has_no_button_and_cancel_and_forged(fake: dict[str, list[Any]]) -> None:
    bot = FakeBot()
    assert not tb.handle_stock(bot, _m("개별주 BAD"))  # type: ignore[arg-type]
    assert bot.out[-1][0] == "send" and "사지 않아요" in bot.out[-1][1]
    tb.handle_stock(bot, _m("개별주 NVDA"))  # type: ignore[arg-type]
    cancel = bot.out[-1][1][1][1][0]["callback_data"]
    tb.handle_stock(bot, _m(data=cancel))  # type: ignore[arg-type]
    assert "취소했어요" in bot.out[-1][1] and not fake["reserve"]
    tb.handle_stock(bot, _m(data="stk:deadbeef:NVDA"))  # type: ignore[arg-type]
    assert not fake["reserve"]


def test_setting_off_blocks_everything(
    fake: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(user_settings, "get", lambda root, key: False)
    bot = FakeBot()
    assert not tb.handle_stock(bot, _m("개별주 NVDA"))  # type: ignore[arg-type]
    assert "꺼져 있어요" in bot.out[-1][1] and not fake["advise"]


def test_bare_word_shows_status_and_release(
    fake: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = FakeBot()
    assert not tb.handle_stock(bot, _m("개별주"))  # type: ignore[arg-type]
    assert "감시 중인 종목 없음" in bot.out[-1][1]
    held = Ticket("1016", "NVDA", "엔비디아", "USD", 8, 200.0, 0, status="holding")
    monkeypatch.setattr(sg, "load_tickets", lambda: [held])
    monkeypatch.setattr(sg, "status", lambda: "🛡 엔비디아 8주")
    assert tb.handle_stock(bot, _m("개별주"))  # type: ignore[arg-type]
    rel = bot.out[-1][1][1][0][0]["callback_data"]
    tb.handle_stock(bot, _m(data=rel))  # type: ignore[arg-type]
    assert fake["release"] == [("1016", "released")] and "손절 해제" in bot.out[-1][1]
