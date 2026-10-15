"""받은 글 대기열: 명령은 봇이, 나머지(금액 · 버튼)는 Deneb 이 — 서로 지우지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_trading.notify import inbox as inbox_mod
from auto_trading.notify.inbox import InboxMessage, InboxQueue, is_command


class FakeInbox:
    def __init__(self, batches: list[list[InboxMessage]]) -> None:
        self.batches = batches
        self.offsets: list[int] = []

    def fetch(self, offset: int) -> tuple[list[InboxMessage], int]:
        self.offsets.append(offset)
        out = self.batches.pop(0) if self.batches else []
        return out, max([offset, *(m.update_id + 1 for m in out)])


def test_is_command_only_plain_status_words() -> None:
    assert is_command(InboxMessage(1, 0, "현황"))
    assert is_command(InboxMessage(1, 0, " /Status "))
    assert not is_command(InboxMessage(1, 0, "30만"))
    assert not is_command(InboxMessage(1, 0, "", data="현황"))  # 버튼은 Deneb 몫


def test_two_readers_share_one_offset(tmp_path: Path) -> None:
    path = tmp_path / "inbox.json"
    fake = FakeInbox(
        [
            [
                InboxMessage(10, 0, "현황"),
                InboxMessage(11, 0, "30만"),
                InboxMessage(12, 0, "", data="p"),
            ]
        ]
    )
    bot = InboxQueue(path, start_offset=7).take(fake, is_command)  # type: ignore[arg-type]
    assert [m.text for m in bot] == ["현황"]
    deneb = InboxQueue(path, start_offset=999).take(fake, lambda m: not is_command(m))  # type: ignore[arg-type]
    assert [m.update_id for m in deneb] == [11, 12]
    assert fake.offsets == [7, 13]  # 두 번째는 파일의 offset (start_offset 무시)
    assert json.loads(path.read_text(encoding="utf-8"))["pending"] == []
    assert not path.with_suffix(".lock").exists()


def test_unclaimed_messages_expire_after_a_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "inbox.json"
    clock = [1_000_000.0]
    monkeypatch.setattr("auto_trading.notify.inbox.time.time", lambda: clock[0])
    fake = FakeInbox([[InboxMessage(1, 0, "안녕")]])
    InboxQueue(path).take(fake, is_command)  # type: ignore[arg-type]
    assert len(json.loads(path.read_text(encoding="utf-8"))["pending"]) == 1
    clock[0] += inbox_mod.KEEP_S + 1
    assert InboxQueue(path).take(fake, is_command) == []  # type: ignore[arg-type]
    assert json.loads(path.read_text(encoding="utf-8"))["pending"] == []
