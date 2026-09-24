"""사용자 스트림 연결 수명: 이벤트 전달·연장·만료·끊김 재연결·키 가림 (네트워크 없음)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.exchange.binance_usdm import BinanceUsdmReadOnly
from auto_trading.exchange.binance_usdm_stream import (
    TESTNET_PRIVATE_WS,
    UserStream,
    stream_url,
)
from auto_trading.exchange.user_stream import AlgoUpdate, UserEvent

ALGO = {
    "e": "ALGO_UPDATE",
    "E": 1,
    "o": {"caid": "c1", "aid": 9, "s": "ETHUSDT", "S": "SELL", "X": "NEW", "tp": "1"},
}


class Keys:
    def __init__(self) -> None:
        self.started = 0
        self.keepalives = 0
        self.closed = 0
        self.fail_keepalive = False

    def start_user_stream(self) -> str:
        self.started += 1
        return f"SECRETKEY{self.started}"

    def keepalive_user_stream(self) -> None:
        self.keepalives += 1
        if self.fail_keepalive:
            raise RuntimeError("listenKey SECRETKEY1 does not exist")

    def close_user_stream(self) -> None:
        self.closed += 1


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Script:
    """연결마다 받을 메시지 목록. 항목이 None 이면 시간 초과, 예외면 끊김."""

    def __init__(self, sessions: list[Any], stop: threading.Event, clock: Clock) -> None:
        self.sessions = sessions
        self.stop = stop
        self.clock = clock
        self.urls: list[str] = []
        self.tick = 1.0

    @contextmanager
    def connect(self, url: str) -> Iterator[Any]:
        self.urls.append(url)
        if not self.sessions:
            self.stop.set()
            raise ConnectionError("no more sessions")
        messages = self.sessions.pop(0)
        if isinstance(messages, Exception):
            raise messages
        script = self

        class Conn:
            closed = False

            def recv(self, timeout: float | None = None) -> str:
                script.clock.now += script.tick
                if self.closed:
                    raise ConnectionError("closed")
                if not messages:
                    if not script.sessions:
                        script.stop.set()
                    raise TimeoutError
                item = messages.pop(0)
                if item is None:
                    raise TimeoutError
                if isinstance(item, Exception):
                    raise item
                return item if isinstance(item, str) else json.dumps(item)

            def close(self) -> None:
                self.closed = True

        yield Conn()


def run(
    sessions: list[Any], **kwargs: Any
) -> tuple[UserStream, Keys, Script, dict[str, list[Any]]]:
    stop = threading.Event()
    clock = Clock()
    script = Script(sessions, stop, clock)
    keys = Keys()
    stream = UserStream(
        keys,
        TESTNET_PRIVATE_WS,
        connector=script.connect,
        clock=clock,
        backoff_s=(0.0,),
        **kwargs,
    )
    seen: dict[str, list[Any]] = {"events": [], "connects": [], "disconnects": [], "bad": []}

    def on_event(e: UserEvent) -> None:
        seen["events"].append(e)

    stream.run(
        on_event,
        stop=stop,
        on_connect=lambda: seen["connects"].append(1),
        on_disconnect=seen["disconnects"].append,
        on_bad_message=lambda raw, err: seen["bad"].append((raw, err)),
    )
    return stream, keys, script, seen


def test_url_uses_private_route_and_events() -> None:
    url = stream_url(TESTNET_PRIVATE_WS, "K")
    assert url == (
        "wss://fstream.binancefuture.com/private/ws?listenKey=K"
        "&events=ORDER_TRADE_UPDATE/ACCOUNT_UPDATE/ALGO_UPDATE"
    )


def test_events_are_delivered_and_key_closed_at_stop() -> None:
    stream, keys, script, seen = run([[ALGO, None, ALGO]])
    assert [type(e) for e in seen["events"]] == [AlgoUpdate, AlgoUpdate]
    assert seen["connects"] == [1]
    assert stream.stats.events == 2
    assert keys.closed == 1
    assert "listenKey=SECRETKEY1" in script.urls[0]


def test_bad_message_is_counted_not_fatal_and_key_masked() -> None:
    bad = '{"e": "ORDER_TRADE_UPDATE", "o": {}, "k": "SECRETKEY1"}'
    stream, _, _, seen = run([["not json", bad, ALGO]])
    assert stream.stats.parse_errors == 2
    assert len(seen["events"]) == 1
    assert all("SECRETKEY1" not in raw for raw, _ in seen["bad"])


def test_listen_key_expired_reconnects_with_new_key() -> None:
    _, keys, script, seen = run([[{"e": "listenKeyExpired", "E": 3}], [ALGO]])
    assert keys.started == 2
    assert seen["disconnects"][0] == "expired"
    assert len(seen["connects"]) == 2  # 다시 연결할 때마다 전체 재조회 신호
    assert "SECRETKEY2" in script.urls[1]


def test_keepalive_before_sixty_minutes() -> None:
    _, keys, _, _ = run([[None] * 5], keepalive_s=2.0)
    assert keys.keepalives >= 2


def test_keepalive_failure_reconnects_and_masks_key() -> None:
    stop = threading.Event()
    clock = Clock()
    script = Script([[None, None, None], [ALGO]], stop, clock)
    keys = Keys()
    keys.fail_keepalive = True
    stream = UserStream(
        keys, TESTNET_PRIVATE_WS, connector=script.connect, clock=clock, keepalive_s=1.5,
        backoff_s=(0.0,),
    )  # fmt: skip
    reasons: list[str] = []
    stream.run(lambda e: None, stop=stop, on_disconnect=reasons.append)
    assert keys.started >= 2
    assert reasons and "SECRETKEY1" not in reasons[0] and "<listenKey>" in reasons[0]


def test_session_rotates_before_24h() -> None:
    _, keys, _, seen = run([[None] * 10, [ALGO]], max_session_s=3.0)
    assert "rotate" in seen["disconnects"]
    assert keys.started == 2


def test_connect_failure_retries() -> None:
    _, keys, _, seen = run([ConnectionError("down"), [ALGO]])
    assert "down" in seen["disconnects"][0]
    assert len(seen["events"]) == 1
    assert keys.started == 2


def test_drop_forces_reconnect() -> None:
    stop = threading.Event()
    clock = Clock()
    script = Script([[None, None], [ALGO]], stop, clock)
    stream = UserStream(Keys(), TESTNET_PRIVATE_WS, connector=script.connect, clock=clock,
                        backoff_s=(0.0,))  # fmt: skip
    events: list[UserEvent] = []

    def on_connect() -> None:
        if stream.stats.connects == 1:
            stream.drop()

    stream.run(events.append, stop=stop, on_connect=on_connect)
    assert stream.stats.connects == 2
    assert len(events) == 1


def test_adapter_listen_key_calls_send_key_header_without_signature() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"listenKey": "abc"})
        return httpx.Response(200, json={})

    api = BinanceUsdmReadOnly(
        BinanceCredentials("the-key", "the-secret"),
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    assert api.start_user_stream() == "abc"
    api.keepalive_user_stream()
    api.close_user_stream()
    assert [r.method for r in seen] == ["POST", "PUT", "DELETE"]
    for r in seen:
        assert r.url.path == "/fapi/v1/listenKey"
        assert r.headers["X-MBX-APIKEY"] == "the-key"
        assert "signature" not in str(r.url)
