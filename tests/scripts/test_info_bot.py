"""정보 봇: 정보 봇 토큰이 없으면 아무것도 안 읽는다(투자 봇 글을 지우지 않게)."""

from __future__ import annotations

from pathlib import Path

import pytest
import scripts.info_bot as mod

from auto_trading.notify.inbox import InboxMessage


def test_no_info_token_does_nothing(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    assert mod.info_credentials(tmp_path) is None
    assert mod.run(tmp_path) == 0
    assert not (tmp_path / "data").exists()


def test_replies_to_each_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "TELEGRAM_INFO_BOT_TOKEN=y\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8"
    )
    sent: list[str] = []
    box = [InboxMessage(1, 0, "/start"), InboxMessage(2, 0, "삼성전자")]

    class FakeInbox:
        def __init__(self, creds: object) -> None:
            pass

        def fetch(self, offset: int) -> tuple[list[InboxMessage], int]:
            out = list(box)
            box.clear()
            return out, 3

    class FakeBot:
        def __init__(self, creds: object) -> None:
            pass

        def send(self, text: str) -> None:
            sent.append(text)

        def close(self) -> None:
            pass

    monkeypatch.setattr(mod, "TelegramInbox", FakeInbox)
    monkeypatch.setattr(mod, "TelegramNotifier", FakeBot)
    monkeypatch.setattr(mod, "fetch_news", lambda target: [])
    monkeypatch.setattr(mod, "fetch_price", lambda target: "")
    assert mod.run(tmp_path) == 0
    assert sent[0].startswith("🪐 정보 봇")
    assert "뉴스 찾는 중" in sent[1] and "찾지 못했어요" in sent[2]
