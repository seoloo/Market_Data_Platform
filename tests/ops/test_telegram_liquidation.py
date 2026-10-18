"""텔레그램 '정리' · '정지풀기' — 미리 보기 + 범위 버튼(5 분 · 한 번) → 실행기. 가짜 봇 · 실행기."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_trading.notify.inbox import InboxMessage
from auto_trading.ops.liquidate import Store
from scripts import liquidate as liq
from scripts import telegram_bot as tb


class FakeBot:
    def __init__(self) -> None:
        self.out: list[tuple[str, Any]] = []

    def send(self, text: str) -> None:
        self.out.append(("send", text))

    def send_message(self, text: str, *, buttons: Any = None, **_: Any) -> int:
        self.out.append(("buttons", (text, buttons)))
        return 7

    def edit(self, message_id: int, text: str, **_: Any) -> None:
        self.out.append(("edit", text))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.out.append(("answer", text))


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"start": [], "unhalt": []}
    monkeypatch.setattr(liq, "STORE", Store(tmp_path / "liq"))
    monkeypatch.setattr(liq, "preview_text", lambda scope="all": "🧯 미리 보기")

    def start(scope: str, src: str) -> str:
        calls["start"].append((scope, src))
        return "ok"

    def unhalt(group: str, src: str) -> str:
        calls["unhalt"].append((group, src))
        return "ok"

    monkeypatch.setattr(liq, "start", start)
    monkeypatch.setattr(liq, "unhalt", unhalt)
    monkeypatch.setattr(liq, "halts", lambda: {"vega": "전부 정리", "deneb": None, "acc": None})
    return calls


def _press(data: str) -> InboxMessage:
    return InboxMessage(2, 0, "", data=data, callback_id="cb", message_id=7)


def test_liquidate_needs_button_and_runs_once(fake: dict[str, list[Any]]) -> None:
    bot = FakeBot()
    assert tb.handle_liquidation(bot, InboxMessage(1, 0, "정리"))  # type: ignore[arg-type]
    kind, (text, buttons) = bot.out[-1]
    assert kind == "buttons" and "미리 보기" in text and not fake["start"]
    data = [b["callback_data"] for row in buttons for b in row]
    assert [d.rsplit(":", 1)[1] for d in data] == [
        "all",
        "vega",
        "deneb",
        "acc",
        "stock",
        "altair",
        "sirius",
        "cancel",
    ]
    deneb = next(d for d in data if d.endswith(":deneb"))
    assert not tb.handle_liquidation(bot, _press(deneb))  # type: ignore[arg-type]
    assert fake["start"] == [("deneb", "텔레그램")]
    tb.handle_liquidation(bot, _press(deneb))  # type: ignore[arg-type]  # 두 번째 누름
    assert fake["start"] == [("deneb", "텔레그램")] and "지난 버튼" in bot.out[-1][1]


def test_cancel_button_and_forged_nonce(fake: dict[str, list[Any]]) -> None:
    bot = FakeBot()
    tb.handle_liquidation(bot, InboxMessage(1, 0, "전부매도"))  # type: ignore[arg-type]
    cancel = next(
        b["callback_data"]
        for row in bot.out[-1][1][1]
        for b in row
        if b["callback_data"].endswith(":cancel")
    )
    tb.handle_liquidation(bot, _press(cancel))  # type: ignore[arg-type]
    assert not fake["start"] and "취소했어요" in bot.out[-1][1]
    tb.handle_liquidation(bot, _press("liq:deadbeef:all"))  # type: ignore[arg-type]
    assert not fake["start"]


def test_unhalt_lists_only_halted_groups(fake: dict[str, list[Any]]) -> None:
    bot = FakeBot()
    assert tb.handle_liquidation(bot, InboxMessage(1, 0, "정지풀기"))  # type: ignore[arg-type]
    text, buttons = bot.out[-1][1]
    assert "Vega" in text and len(buttons) == 2  # Vega 풀기 + 취소
    tb.handle_liquidation(bot, _press(buttons[0][0]["callback_data"]))  # type: ignore[arg-type]
    assert fake["unhalt"] == [("vega", "텔레그램")]


def test_help_words_route_to_bot_and_pin_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.notify.inbox import is_command

    for w in ("도움", "/help", "/start", "사용법"):
        assert is_command(InboxMessage(1, 0, w))  # Deneb 대화가 가져가지 않는다
    assert "정리" in tb.HELP and "현황" in tb.HELP and "정지풀기" in tb.HELP

    calls: list[Any] = []

    class Bot:
        def __init__(self, creds: object) -> None:
            pass

        def send_message(self, text: str, **_: Any) -> int:
            calls.append(("send", text))
            return 55

        def unpin_all(self) -> None:
            calls.append(("unpin_all",))

        def pin(self, mid: int) -> None:
            calls.append(("pin", mid))

        def set_commands(self, commands: list[tuple[str, str]]) -> None:
            calls.append(("menu", [c for c, _ in commands]))

        def close(self) -> None:
            pass

    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    monkeypatch.setattr(tb, "TelegramNotifier", Bot)
    assert "고정" in tb.pin_help(tmp_path)
    assert calls[0][0] == "send" and calls[1:3] == [("unpin_all",), ("pin", 55)]
    assert calls[3] == (
        "menu",
        ["status", "paper", "stock", "deposit", "liquidate", "unhalt", "help"],
    )


def test_single_symbol_liquidation_button(
    fake: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.notify.inbox import is_liquidation, liquidation_symbol
    from scripts import size_guard as sg

    assert liquidation_symbol(InboxMessage(1, 0, "정리 nvda")) == "nvda"
    assert is_liquidation(InboxMessage(1, 0, "청산 삼성전자"))
    assert liquidation_symbol(InboxMessage(1, 0, "정리")) == ""
    monkeypatch.setattr(sg, "resolve", lambda q: ("NVDA", "엔비디아"))
    previews: list[str] = []

    def preview(scope: str = "all") -> str:
        previews.append(scope)
        return "미리"

    monkeypatch.setattr(liq, "preview_text", preview)
    bot = FakeBot()
    assert tb.handle_liquidation(bot, InboxMessage(1, 0, "정리 nvda"))  # type: ignore[arg-type]
    assert previews == ["sym:NVDA"]
    buttons = bot.out[-1][1][1]
    assert buttons[0][0]["text"] == "🧯 엔비디아 정리"
    tb.handle_liquidation(bot, _press(buttons[0][0]["callback_data"]))  # type: ignore[arg-type]
    assert fake["start"] == [("sym:NVDA", "텔레그램")]
