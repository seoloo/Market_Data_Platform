"""알림 채널 (Phase 6 게이트: 알림·일일 보고).

두 채널을 둔다.

- :class:`OutboxNotifier` — 로컬 파일에 덧붙인다. **항상 켠다.** 텔레그램이 설정되지 않았거나
  네트워크가 죽어도 알림이 사라지지 않게 하는 바닥 채널이다.
- :class:`TelegramNotifier` — ``.env``에 토큰과 채팅 ID가 있을 때만 쓴다.

텔레그램 토큰은 URL 경로에 들어간다. 그래서 전송 실패를 알릴 때 **원래 예외를 연결하지 않는다**
(``from None``) — httpx 예외 메시지와 traceback 에 URL(= 토큰)이 실릴 수 있기 때문이다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx

from auto_trading.config.secrets import TelegramCredentials
from auto_trading.core.errors import AutoTradingError

__all__ = ["TELEGRAM_LIMIT", "Notifier", "NotifyError", "OutboxNotifier", "TelegramNotifier"]

TELEGRAM_LIMIT = 4096
"""텔레그램 메시지 최대 길이 (문자)."""


class NotifyError(AutoTradingError):
    """알림 전송 실패. 메시지에 토큰을 싣지 않는다."""


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class OutboxNotifier:
    """``path``에 시각과 함께 덧붙인다. 파일은 저장소 밖 산출물(reports/)에 둔다."""

    __slots__ = ("_clock", "path")

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock

    def send(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._clock()))
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"===== {stamp} =====\n{text.rstrip()}\n\n")


class TelegramNotifier:
    __slots__ = ("_client", "_creds", "base_url")

    def __init__(
        self,
        credentials: TelegramCredentials,
        *,
        client: httpx.Client | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self._creds = credentials
        self._client = client if client is not None else httpx.Client(timeout=10.0)
        self.base_url = base_url.rstrip("/")

    def __repr__(self) -> str:
        return f"TelegramNotifier({self._creds!r})"

    def send(self, text: str) -> None:
        if len(text) > TELEGRAM_LIMIT:
            text = text[: TELEGRAM_LIMIT - 20] + "\n…(잘림, 로컬 기록 참고)"
        url = f"{self.base_url}/bot{self._creds.bot_token}/sendMessage"
        try:
            response = self._client.post(url, json={"chat_id": self._creds.chat_id, "text": text})
        except httpx.HTTPError as exc:
            raise NotifyError(f"텔레그램 전송 실패 ({type(exc).__name__})") from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not response.is_success or not body.get("ok", False):
            desc = str(body.get("description", ""))[:200].replace(self._creds.bot_token, "***")
            raise NotifyError(f"텔레그램 전송 거부 {response.status_code}: {desc}")

    def close(self) -> None:
        self._client.close()
