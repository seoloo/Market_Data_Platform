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


def test_fetch_reads_button_presses_and_replies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 1,
                        "callback_query": {
                            "id": "cb9",
                            "data": "h2026:all",
                            "message": {"chat": {"id": 42}, "date": 5, "message_id": 30},
                        },
                    },
                    {
                        "update_id": 2,
                        "callback_query": {
                            "id": "x",
                            "data": "h2026:all",
                            "message": {"chat": {"id": 99}, "date": 5, "message_id": 31},
                        },
                    },
                    {
                        "update_id": 3,
                        "message": {
                            "chat": {"id": 42},
                            "date": 6,
                            "text": "2",
                            "message_id": 40,
                            "reply_to_message": {"message_id": 30},
                        },
                    },
                ],
            },
        )

    inbox = TelegramInbox(
        TelegramCredentials("TOKEN", "42"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    msgs, nxt = inbox.fetch(1)
    assert nxt == 4 and len(msgs) == 2
    assert (msgs[0].data, msgs[0].callback_id, msgs[0].message_id, msgs[0].text) == (
        "h2026:all",
        "cb9",
        30,
        "",
    )
    assert (msgs[1].text, msgs[1].reply_to, msgs[1].data) == ("2", 30, "")


def test_liquidation_words_and_buttons_go_to_bot_not_deneb() -> None:
    from auto_trading.notify.inbox import InboxMessage, is_command, is_liquidation

    def m(text: str = "", data: str = "") -> InboxMessage:
        return InboxMessage(1, 0, text, data=data)

    for t in ("정리", "전부 매도", "/liquidate", "정지풀기"):
        assert is_liquidation(m(t)) and is_command(m(t))
    assert is_liquidation(m(data="liq:ab12:all")) and is_command(m(data="unh:ab12:vega"))
    assert is_command(m("현황")) and not is_liquidation(m("현황"))
    assert not is_command(m("30만")) and not is_command(m(data="p1:all"))  # Deneb 몫


def test_deposit_word_routes_to_bot() -> None:
    from auto_trading.notify.inbox import InboxMessage, is_command

    assert is_command(InboxMessage(1, 0, "입금확인")) and is_command(InboxMessage(1, 0, "/deposit"))
    assert not is_command(InboxMessage(1, 0, "입금"))  # Deneb 금액 질문과 헷갈리지 않게
