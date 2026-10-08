from __future__ import annotations

import httpx
import pytest

from auto_trading.config.secrets import TelegramCredentials
from auto_trading.notify.inbox import TelegramInbox, parse_krw


@pytest.mark.parametrize(
    ("text", "krw"),
    [
        ("30만", 300_000.0),
        ("30만원", 300_000.0),
        ("50만 5천", 505_000.0),
        ("500,000", 500_000.0),
        ("500000원", 500_000.0),
        ("70", 700_000.0),
        ("0", 0.0),
        ("이번 달은 쉬기", 0.0),
        ("안녕", None),
        ("5천", None),
    ],
)
def test_parse_krw(text: str, krw: float | None) -> None:
    assert parse_krw(text) == krw


def test_fetch_only_registered_chat_and_advances_offset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["offset"] == "5"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 5, "message": {"chat": {"id": 42}, "date": 10, "text": " 30만 "}},
                    {"update_id": 6, "message": {"chat": {"id": 99}, "date": 11, "text": "100만"}},
                    {"update_id": 7, "message": {"chat": {"id": 42}, "date": 12}},
                ],
            },
        )

    inbox = TelegramInbox(
        TelegramCredentials("TOKEN", "42"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    msgs, nxt = inbox.fetch(5)
    assert [(m.update_id, m.text) for m in msgs] == [(5, "30만")]
    assert nxt == 8
