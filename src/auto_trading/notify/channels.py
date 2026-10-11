"""알림 채널 (Phase 6 게이트: 알림·일일 보고).

두 채널을 둔다.

- :class:`OutboxNotifier` — 로컬 파일에 덧붙인다. **항상 켠다.** 텔레그램이 설정되지 않았거나
  네트워크가 죽어도 알림이 사라지지 않게 하는 바닥 채널이다.
- :class:`TelegramNotifier` — ``.env``에 토큰과 채팅 ID가 있을 때만 쓴다.

텔레그램 토큰은 URL 경로에 들어간다. 그래서 전송 실패를 알릴 때 **원래 예외를 연결하지 않는다**
(``from None``) — httpx 예외 메시지와 traceback 에 URL(= 토큰)이 실릴 수 있기 때문이다.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

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

    def send_html(self, text: str) -> None:
        """``parse_mode=HTML`` 로 보낸다 (링크를 글자에 건다, 미리 보기 끔).

        부르는 쪽이 바깥 글을 이스케이프한다.
        텔레그램이 태그를 거부하면 태그를 지운 글로 다시 보낸다.
        """
        try:
            self._call(
                "sendMessage",
                {
                    "text": text,
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True},
                },
            )
        except NotifyError:
            plain = re.sub(r"<[^>]+>", "", text)
            self.send(html.unescape(plain))

    # ------------------------------------------------------------ 버튼 · 고정 · 수정

    def _call(self, method: str, payload: dict[str, Any], *, chat: bool = True) -> Any:
        """봇 API 한 번 (결과 ``result`` 를 돌려준다). 오류 문구에 토큰을 싣지 않는다."""
        url = f"{self.base_url}/bot{self._creds.bot_token}/{method}"
        body_in = {"chat_id": self._creds.chat_id, **payload} if chat else dict(payload)
        try:
            response = self._client.post(url, json=body_in)
        except httpx.HTTPError as exc:
            raise NotifyError(f"텔레그램 {method} 실패 ({type(exc).__name__})") from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not response.is_success or not body.get("ok", False):
            desc = str(body.get("description", ""))[:200].replace(self._creds.bot_token, "***")
            raise NotifyError(f"텔레그램 {method} 거부 {response.status_code}: {desc}")
        return body.get("result")

    def send_message(
        self,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
        force_reply: bool = False,
        reply_to: int = 0,
    ) -> int:
        """보내고 메시지 번호를 돌려준다.

        ``buttons`` = 인라인 버튼 줄, ``force_reply`` = 답 입력칸을 바로 연다.
        """
        if len(text) > TELEGRAM_LIMIT:
            text = text[: TELEGRAM_LIMIT - 20] + "\n…(잘림, 로컬 기록 참고)"
        payload: dict[str, Any] = {"text": text}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        elif force_reply:
            payload["reply_markup"] = {
                "force_reply": True,
                "input_field_placeholder": "숫자만 (예: 2)",
            }
        if reply_to:
            payload["reply_parameters"] = {
                "message_id": reply_to,
                "allow_sending_without_reply": True,
            }
        result = self._call("sendMessage", payload)
        return int(result.get("message_id", 0)) if isinstance(result, dict) else 0

    def edit(
        self, message_id: int, text: str, *, buttons: list[list[dict[str, str]]] | None = None
    ) -> None:
        """글을 바꾼다 (알림은 울리지 않는다). ``buttons`` 가 None 이면 버튼을 없앤다."""
        self._call(
            "editMessageText",
            {
                "message_id": message_id,
                "text": text[:TELEGRAM_LIMIT],
                "reply_markup": {"inline_keyboard": buttons or []},
            },
        )

    def pin(self, message_id: int) -> None:
        self._call("pinChatMessage", {"message_id": message_id, "disable_notification": True})

    def unpin(self, message_id: int) -> None:
        self._call("unpinChatMessage", {"message_id": message_id})

    def unpin_all(self) -> None:
        """이 대화방의 고정 메시지를 모두 푼다."""
        self._call("unpinAllChatMessages", {})

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """입력칸의 '/' 메뉴 — (영문 소문자 명령, 설명). 이 봇 전체에 적용."""
        self._call(
            "setMyCommands",
            {"commands": [{"command": c, "description": d} for c, d in commands]},
            chat=False,
        )

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """버튼을 누른 쪽의 '불러오는 중' 표시를 끈다 (늦으면 텔레그램이 거부 — 무시해도 된다)."""
        self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text[:180]},
            chat=False,
        )

    def close(self) -> None:
        self._client.close()
