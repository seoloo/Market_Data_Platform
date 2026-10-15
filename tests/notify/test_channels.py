"""알림 채널: 로컬 기록은 항상 남고, 텔레그램 토큰은 어떤 경로로도 새지 않는다."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from auto_trading.config.secrets import TelegramCredentials, load_telegram_credentials
from auto_trading.notify.channels import (
    TELEGRAM_LIMIT,
    NotifyError,
    OutboxNotifier,
    TelegramNotifier,
)

TOKEN = "123456:SECRET-TOKEN-VALUE"
CREDS = TelegramCredentials(TOKEN, "987654321")


def telegram(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[TelegramNotifier, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return TelegramNotifier(CREDS, client=client), seen


class TestOutbox:
    def test_appends_with_a_timestamp(self, tmp_path: Path) -> None:
        box = OutboxNotifier(tmp_path / "w" / "outbox.log", clock=lambda: 0.0)
        box.send("첫째")
        box.send("둘째\n")
        text = (tmp_path / "w" / "outbox.log").read_text(encoding="utf-8")
        assert text.count("=====") == 4
        assert text.index("첫째") < text.index("둘째")


class TestTelegram:
    def test_posts_the_chat_id_and_text(self) -> None:
        bot, seen = telegram(lambda r: httpx.Response(200, json={"ok": True}))
        bot.send("안녕")
        body = json.loads(seen[0].content)
        assert body == {"chat_id": "987654321", "text": "안녕"}
        assert seen[0].url.path == f"/bot{TOKEN}/sendMessage"

    def test_long_text_is_cut_to_the_limit(self) -> None:
        bot, seen = telegram(lambda r: httpx.Response(200, json={"ok": True}))
        bot.send("가" * (TELEGRAM_LIMIT + 500))
        assert len(json.loads(seen[0].content)["text"]) <= TELEGRAM_LIMIT

    def test_transport_failure_does_not_leak_the_token(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        bot, _ = telegram(boom)
        with pytest.raises(NotifyError) as info:
            bot.send("x")
        assert TOKEN not in str(info.value)
        assert info.value.__cause__ is None
        assert info.value.__suppress_context__  # traceback 에 원래 예외(URL 포함)를 싣지 않는다

    def test_api_rejection_does_not_leak_the_token(self) -> None:
        body = {"ok": False, "description": f"Unauthorized for bot{TOKEN}"}
        bot, _ = telegram(lambda r: httpx.Response(401, json=body))
        with pytest.raises(NotifyError, match="401") as info:
            bot.send("x")
        assert TOKEN not in str(info.value)

    def test_ok_false_with_200_is_still_a_failure(self) -> None:
        bot, _ = telegram(lambda r: httpx.Response(200, json={"ok": False}))
        with pytest.raises(NotifyError):
            bot.send("x")

    def test_repr_hides_the_token(self) -> None:
        bot, _ = telegram(lambda r: httpx.Response(200, json={"ok": True}))
        assert TOKEN not in repr(bot)
        assert TOKEN not in repr(CREDS)
        assert TOKEN not in str(CREDS)


class TestCredentials:
    def test_missing_values_mean_telegram_is_off(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
        assert load_telegram_credentials(env, environ={}) is None

    def test_one_value_alone_is_not_enough(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=abc\n", encoding="utf-8")
        assert load_telegram_credentials(env, environ={}) is None

    def test_environment_overrides_the_file(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=file\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
        creds = load_telegram_credentials(env, environ={"TELEGRAM_BOT_TOKEN": "env"})
        assert creds == TelegramCredentials("env", "1")


class TestTelegramButtons:
    def test_send_message_returns_id_with_buttons_and_reply(self) -> None:
        import json as _json

        bot, seen = telegram(
            lambda r: httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
        )
        mid = bot.send_message(
            "공지", buttons=[[{"text": "네", "callback_data": "p:all"}]], reply_to=5
        )
        body = _json.loads(seen[0].content)
        assert mid == 77 and seen[0].url.path.endswith("/sendMessage")
        assert body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "p:all"
        assert body["reply_parameters"]["message_id"] == 5 and body["chat_id"] == "987654321"

    def test_force_reply_pin_edit_and_callback_answer(self) -> None:
        import json as _json

        bot, seen = telegram(lambda r: httpx.Response(200, json={"ok": True, "result": True}))
        bot.send_message("몇 주?", force_reply=True)
        bot.pin(9)
        bot.edit(9, "바뀜")
        bot.unpin(9)
        bot.answer_callback("cb1", "접수")
        paths = [r.url.path.rsplit("/", 1)[1] for r in seen]
        assert paths == [
            "sendMessage",
            "pinChatMessage",
            "editMessageText",
            "unpinChatMessage",
            "answerCallbackQuery",
        ]
        bodies = [_json.loads(r.content) for r in seen]
        assert bodies[0]["reply_markup"]["force_reply"] is True
        assert bodies[1]["disable_notification"] is True
        assert bodies[2]["reply_markup"] == {"inline_keyboard": []}
        assert "chat_id" not in bodies[4] and bodies[4]["callback_query_id"] == "cb1"

    def test_button_call_rejection_hides_token(self) -> None:
        bot, _ = telegram(
            lambda r: httpx.Response(400, json={"ok": False, "description": f"bad {TOKEN}"})
        )
        with pytest.raises(NotifyError) as err:
            bot.pin(1)
        assert TOKEN not in str(err.value)


def test_send_html_uses_parse_mode_and_falls_back_to_plain() -> None:
    import json as _json

    import httpx as _httpx

    from auto_trading.config.secrets import TelegramCredentials
    from auto_trading.notify.channels import TelegramNotifier

    sent: list[dict[str, object]] = []

    def handle(request: _httpx.Request) -> _httpx.Response:
        body = _json.loads(request.content)
        sent.append(body)
        if body.get("parse_mode") == "HTML":
            return _httpx.Response(400, json={"ok": False, "description": "can't parse entities"})
        return _httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    bot = TelegramNotifier(
        TelegramCredentials("T0K", "42"),
        client=_httpx.Client(transport=_httpx.MockTransport(handle)),
    )
    bot.send_html('<b>굵게</b> <a href="https://x">기사</a> &amp; 끝')
    assert sent[0]["parse_mode"] == "HTML"
    assert sent[0]["link_preview_options"] == {"is_disabled": True}
    assert sent[1]["text"] == "굵게 기사 & 끝" and "parse_mode" not in sent[1]


def test_unpin_all_and_commands_menu() -> None:
    import json as _json

    import httpx as _httpx

    from auto_trading.config.secrets import TelegramCredentials
    from auto_trading.notify.channels import TelegramNotifier

    seen: list[tuple[str, dict[str, object]]] = []

    def handle(request: _httpx.Request) -> _httpx.Response:
        seen.append((request.url.path.rsplit("/", 1)[-1], _json.loads(request.content)))
        return _httpx.Response(200, json={"ok": True, "result": True})

    bot = TelegramNotifier(
        TelegramCredentials("T0K", "42"),
        client=_httpx.Client(transport=_httpx.MockTransport(handle)),
    )
    bot.unpin_all()
    bot.set_commands([("help", "사용법")])
    assert seen[0] == ("unpinAllChatMessages", {"chat_id": "42"})
    cmds = [{"command": "help", "description": "사용법"}]
    assert seen[1] == ("setMyCommands", {"commands": cmds})
    # 휴대폰 앱이 새 목록을 받도록 개인 대화 · 이 대화방 범위까지 (2026-10-16)
    assert seen[2] == ("setMyCommands", {"commands": cmds, "scope": {"type": "all_private_chats"}})
    assert seen[3] == (
        "setMyCommands",
        {"commands": cmds, "scope": {"type": "chat", "chat_id": "42"}},
    )
    assert seen[4] == ("setChatMenuButton", {"chat_id": "42", "menu_button": {"type": "commands"}})
