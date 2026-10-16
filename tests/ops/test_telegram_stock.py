"""텔레그램 '개별주' — 추천 + [이대로 매수] 버튼(5 분 · 한 번) → 예약. 가짜 봇 · 가짜 size_guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_trading.notify import inbox
from auto_trading.notify.inbox import InboxMessage, is_command, is_stock, is_stock_answer
from auto_trading.ops import user_settings
from auto_trading.ops.liquidate import Store
from auto_trading.ops.position_size import Advice
from auto_trading.ops.size_order import Ticket
from scripts import size_guard as sg
from scripts import telegram_bot as tb
from tests.ops.test_telegram_liquidation import FakeBot


def _advice(ok: bool = True, max_qty: int = 0) -> Advice:
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
        max_qty=max_qty,
        fx=1_400.0,
    )


@pytest.fixture(autouse=True)
def ask_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """'어떤 종목?' 질문 기록을 임시 폴더로 — 진짜 data/telegram 을 건드리지 않는다."""
    path = tmp_path / "stock_ask.json"
    monkeypatch.setattr(inbox, "stock_ask_path", lambda: path)
    return path


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"reserve": [], "release": [], "advise": [], "soft": []}
    monkeypatch.setattr(tb, "STOCK_STORE", Store(tmp_path / "tg"))
    monkeypatch.setattr(user_settings, "get", lambda root, key: True)

    def advise(q: str) -> Advice:
        calls["advise"].append(q)
        if q == "SOFT":  # 이 뒤로는 같은 종목(NVDA)도 규칙에 걸린 것으로
            calls["soft"].append(1)
        soft = q == "SOFT" or bool(calls["soft"])
        return _advice(ok=q != "BAD" and not soft, max_qty=19 if q != "BAD" else 0)

    def reserve(q: str, qty: int | None = None) -> str:
        calls["reserve"].append(q if qty is None else (q, qty))
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
    assert buy["text"] == "🛒 8주 매수" and buy["callback_data"].endswith(":NVDA")
    assert buttons[0][1]["text"] == "✏️ 수량 바꾸기"
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


def test_bare_word_asks_which_stock_then_answer_gets_advice(
    fake: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = FakeBot()
    assert tb.handle_stock(bot, _m("/stock"))  # type: ignore[arg-type]
    kind, (text, buttons) = bot.out[-1]
    assert kind == "buttons" and "어떤 종목을 살까요?" in text and buttons is None
    ask = inbox.stock_ask()
    assert ask is not None and ask[0] == 7  # FakeBot 의 메시지 번호
    mid, at = ask
    answer = InboxMessage(2, at + 30, "현대차", reply_to=mid)
    assert is_stock(answer) and is_command(answer)  # Deneb 대화가 가져가지 않는다
    assert tb.handle_stock(bot, answer)  # type: ignore[arg-type]
    assert fake["advise"] == ["현대차"] and inbox.stock_ask() is None  # 한 번 쓰면 지움
    assert "매수" in bot.out[-1][1][1][0][0]["text"]


def test_answer_window_and_amounts_stay_with_deneb() -> None:
    inbox.set_stock_ask(50, 1_000)
    assert is_stock_answer(InboxMessage(1, 1_100, "삼성전자"))  # 답장 안 해도 5 분 안이면
    assert is_stock_answer(InboxMessage(1, 1_100, "아무거나", reply_to=50))
    assert not is_stock_answer(InboxMessage(1, 1_100, "삼성전자", reply_to=49))  # 다른 글에 답장
    assert not is_stock_answer(InboxMessage(1, 1_400, "삼성전자"))  # 5 분 지남
    assert not is_stock_answer(InboxMessage(1, 999, "삼성전자"))  # 묻기 전
    for amount in ("30만", "1000000", "0", "쉬기", "현황"):
        assert not is_stock_answer(InboxMessage(1, 1_100, amount)), amount
    inbox.clear_stock_ask()
    assert not is_stock_answer(InboxMessage(1, 1_100, "삼성전자"))


def test_bare_word_shows_status_and_release(
    fake: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = FakeBot()
    held = Ticket("1016", "NVDA", "엔비디아", "USD", 8, 200.0, 0, status="holding")
    monkeypatch.setattr(sg, "load_tickets", lambda: [held])
    monkeypatch.setattr(sg, "status", lambda: "🛡 엔비디아 8주")
    assert tb.handle_stock(bot, _m("개별주"))  # type: ignore[arg-type]
    status_msg = bot.out[-2][1]
    assert "예약 · 손절 현황" in status_msg[0] and "어떤 종목" in bot.out[-1][1][0]
    rel = status_msg[1][0][0]["callback_data"]
    tb.handle_stock(bot, _m(data=rel))  # type: ignore[arg-type]
    assert fake["release"] == [("1016", "released")] and "손절 해제" in bot.out[-1][1]


def test_override_asks_qty_then_confirms_then_reserves(fake: dict[str, list[Any]]) -> None:
    bot = FakeBot()
    assert tb.handle_stock(bot, _m("개별주 SOFT"))  # type: ignore[arg-type]
    text, buttons = bot.out[-1][1]
    assert "규칙상 지금은 비추천" in text
    assert buttons[0][0]["text"] == "✏️ 그래도 매수 (수량 입력)"
    assert tb.handle_stock(bot, _m(data=buttons[0][0]["callback_data"]))  # type: ignore[arg-type]
    prompt = bot.out[-1][1][0]
    assert "몇 주 살까요?" in prompt and "최대 19주" in prompt and "추천" not in prompt
    info = inbox.stock_ask_info()
    assert info is not None and info["kind"] == "qty" and info["symbol"] == "NVDA"
    mid, at = info["message_id"], info["at"]
    assert not is_stock_answer(InboxMessage(3, at + 10, "5"))  # 답장 아닌 숫자는 Deneb 몫
    assert is_stock_answer(InboxMessage(3, at + 10, "5주"))
    answer = InboxMessage(3, at + 10, "5", reply_to=mid)
    assert is_stock(answer)
    assert tb.handle_stock(bot, answer)  # type: ignore[arg-type]
    card, rows = bot.out[-1][1]
    assert "엔비디아 5주" in card and "규칙 밖 매수" in card
    assert rows[0][0]["text"] == "✅ 5주 예약" and not fake["reserve"]
    tb.handle_stock(bot, _m(data=rows[0][0]["callback_data"]))  # type: ignore[arg-type]
    assert fake["reserve"] == [("NVDA", 5)]


def test_qty_over_max_and_hard_block(fake: dict[str, list[Any]]) -> None:
    bot = FakeBot()
    inbox.set_stock_ask(70, 1_000, kind="qty", symbol="NVDA")
    assert tb.handle_stock(bot, InboxMessage(4, 1_010, "20", reply_to=70)) is False  # type: ignore[arg-type]
    assert "1 ~ 19주만" in bot.out[-1][1]
    tb.handle_stock(bot, _m("개별주 BAD"))  # type: ignore[arg-type]
    assert bot.out[-1][0] == "send"  # 넘을 수 없는 이유 — 버튼 없음
