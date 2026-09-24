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
