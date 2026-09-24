"""Binance USD-M 사용자 스트림 연결 (WebSocket). 주문을 보내지 않는다 — 받기만 한다.

주소는 2026-04-23 부터 경로가 나뉜 새 구조만 쓴다(옛 ``/ws/<listenKey>`` 는 폐지, 공식 공지).
사용자 데이터는 ``/private`` 이고 받을 이벤트를 ``events`` 로 고른다.

주소 (2026-09-24 확인):

* 실계좌 ``wss://fstream.binance.com/private/ws?listenKey=<키>&events=...`` (공식 문서)
* 테스트넷 ``wss://fstream.binancefuture.com/private/ws?...`` — 문서에 없어 **실측**으로 확인.
  listenKey 는 ``demo-fapi`` 에서 받는다.

수명 규칙:

* listenKey 는 60분 안에 연장하지 않으면 스트림이 닫힌다 → 30분마다 연장한다.
* 한 연결은 24시간을 넘지 못한다 → 23시간에 스스로 새로 연결한다.
* 끊기면(네트워크·연장 실패·``listenKeyExpired``) 새 키로 다시 연결한다.
  오류가 이어지면 기다리는 시간을 늘린다.

**끊긴 동안의 이벤트는 오지 않는다.** 그래서 연결될 때마다 ``on_connect`` 를 부르고, 받는 쪽은
거래소 조회로 전체를 다시 맞춘다(:mod:`auto_trading.execution.stream_sync`). 스트림은 빠른 신호이고,
정기 조회가 빠진 것을 메운다 (TRADING_CORE: 중복·역순 WS 이벤트 멱등 반영 + 정기 REST 대조).

오류 메시지에서 listenKey 를 가린다 — 키만 있으면 누구나 계좌 이벤트를 받을 수 있다.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from auto_trading.exchange.user_stream import ListenKeyExpired, UserEvent, parse_user_event

__all__ = [
    "MAINNET_PRIVATE_WS",
    "STREAM_EVENTS",
    "TESTNET_PRIVATE_WS",
    "ListenKeySource",
    "StreamStats",
    "UserStream",
    "stream_url",
]

MAINNET_PRIVATE_WS = "wss://fstream.binance.com/private/ws"
TESTNET_PRIVATE_WS = "wss://fstream.binancefuture.com/private/ws"
STREAM_EVENTS = ("ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "ALGO_UPDATE")
_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)


class ListenKeySource(Protocol):
    def start_user_stream(self) -> str: ...

    def keepalive_user_stream(self) -> None: ...

    def close_user_stream(self) -> None: ...


class Connection(Protocol):
    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


Connector = Callable[[str], AbstractContextManager[Connection]]


def stream_url(base: str, listen_key: str, events: Sequence[str] = STREAM_EVENTS) -> str:
    return f"{base}?listenKey={listen_key}&events={'/'.join(events)}"


def _default_connector(url: str) -> AbstractContextManager[Connection]:
    from websockets.sync.client import connect  # 선택적 의존 — 스트림을 쓸 때만 필요

    return connect(url, open_timeout=10, close_timeout=5)


@dataclass(slots=True)
class StreamStats:
    connects: int = 0
    disconnects: int = 0
    keepalives: int = 0
    events: int = 0
    parse_errors: int = 0
    last_reason: str = ""


class UserStream:
    """사용자 스트림 한 개. :meth:`run` 은 ``stop`` 이 설정될 때까지 돌아간다 (스레드에서 실행)."""

    def __init__(
        self,
        keys: ListenKeySource,
        ws_base: str,
        *,
        connector: Connector | None = None,
        clock: Callable[[], float] = time.monotonic,
        keepalive_s: float = 30 * 60,
        max_session_s: float = 23 * 3600,
        backoff_s: Sequence[float] = _BACKOFF_S,
        recv_timeout_s: float = 1.0,
    ) -> None:
        self._keys = keys
        self._base = ws_base
        self._connector = connector if connector is not None else _default_connector
        self._clock = clock
        self._keepalive_s = keepalive_s
        self._max_session_s = max_session_s
        self._backoff = tuple(backoff_s)
        self._recv_timeout = recv_timeout_s
        self._conn: Connection | None = None
        self._lock = threading.Lock()
        self.stats = StreamStats()

    def drop(self) -> None:
        """지금 연결을 끊는다 (훈련용 — 재연결 경로 확인). 스트림은 다시 연결한다."""
        with self._lock:
            conn = self._conn
        if conn is not None:
            conn.close()

    def run(
        self,
        on_event: Callable[[UserEvent], None],
        *,
        stop: threading.Event,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
        on_bad_message: Callable[[str, str], None] | None = None,
    ) -> None:
        failures = 0
        key = ""
        while not stop.is_set():
            reason = "stopped"
            try:
                key = self._keys.start_user_stream()
                with self._connector(stream_url(self._base, key)) as conn:
                    with self._lock:
                        self._conn = conn
                    self.stats.connects += 1
                    failures = 0  # 연결에 성공했다 — 다음 끊김은 첫 대기부터
                    if on_connect is not None:
                        on_connect()
                    reason = self._session(conn, key, on_event, stop, on_bad_message)
            except Exception as exc:  # 네트워크·연장 실패·받는 쪽 오류 — 새 키로 다시 연결한다
                reason = f"{type(exc).__name__}: {exc}"
                if key:
                    reason = reason.replace(key, "<listenKey>")
            finally:
                with self._lock:
                    self._conn = None
            if stop.is_set():
                break
            self.stats.disconnects += 1
            self.stats.last_reason = reason
            if on_disconnect is not None:
                on_disconnect(reason)
            if reason in {"rotate", "expired"}:
                continue
            stop.wait(self._backoff[min(failures, len(self._backoff) - 1)])
            failures += 1
        if key:
            # 끝낼 때 키를 닫는다. 실패해도 60분 뒤 스스로 만료된다
            with contextlib.suppress(Exception):
                self._keys.close_user_stream()

    def _session(
        self,
        conn: Connection,
        key: str,
        on_event: Callable[[UserEvent], None],
        stop: threading.Event,
        on_bad_message: Callable[[str, str], None] | None,
    ) -> str:
        started = last_keepalive = self._clock()
        while not stop.is_set():
            now = self._clock()
            if now - started >= self._max_session_s:
                return "rotate"
            if now - last_keepalive >= self._keepalive_s:
                self._keys.keepalive_user_stream()
                self.stats.keepalives += 1
                last_keepalive = now
            try:
                raw = conn.recv(timeout=self._recv_timeout)
            except TimeoutError:
                continue
            text = raw.decode() if isinstance(raw, bytes) else raw
            try:
                event = parse_user_event(json.loads(text))
            except (ValueError, KeyError, TypeError) as exc:
                self.stats.parse_errors += 1
                if on_bad_message is not None:
                    on_bad_message(text.replace(key, "<listenKey>"), f"{type(exc).__name__}: {exc}")
                continue
            self.stats.events += 1
            if isinstance(event, ListenKeyExpired):
                return "expired"
            on_event(event)
        return "stopped"
