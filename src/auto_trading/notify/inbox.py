"""텔레그램 봇이 받은 글 읽기 (getUpdates, 웹훅 없음).

* 등록된 채팅(TELEGRAM_CHAT_ID)의 글만 돌려준다.

* 토큰은 주소에만 쓰고 출력 · 파일에 남기지 않는다(오류 문구에서도 가린다).
* ``offset`` 을 넘기면 그 앞 글은 텔레그램 서버에서 지워진다 — 같은 글을 두 번 처리하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from auto_trading.config.secrets import TelegramCredentials
from auto_trading.notify.channels import NotifyError


@dataclass(frozen=True, slots=True)
class InboxMessage:
    update_id: int
    date: int
    """유닉스 초."""
    text: str


class TelegramInbox:
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

    def fetch(self, offset: int) -> tuple[list[InboxMessage], int]:
        """(등록 채팅의 새 글, 다음 offset)."""
        url = f"{self.base_url}/bot{self._creds.bot_token}/getUpdates"
        try:
            response = self._client.get(url, params={"offset": offset, "timeout": 0})
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise NotifyError(f"텔레그램 받기 실패 ({type(exc).__name__})") from None
        if not response.is_success or not body.get("ok", False):
            desc = str(body.get("description", ""))[:200].replace(self._creds.bot_token, "***")
            raise NotifyError(f"텔레그램 받기 실패 {response.status_code}: {desc}")
        out: list[InboxMessage] = []
        nxt = offset
        for upd in body.get("result") or []:
            uid = int(upd.get("update_id", 0))
            nxt = max(nxt, uid + 1)
            msg = upd.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text")
            if chat == str(self._creds.chat_id) and isinstance(text, str):
                out.append(InboxMessage(uid, int(msg.get("date", 0)), text.strip()))
        return out, nxt


SKIP_WORDS = ("쉬", "건너", "안 사", "안사", "패스", "skip")


def parse_krw(text: str) -> float | None:
    """글 → 원 (순수). 못 읽으면 None.

    '30만' · '30만원' · '500,000' · '50'(만 원 단위로 봄) · '0' · '쉬기'(0).
    """
    t = text.strip().lower().replace(",", "").replace(" ", "")
    if any(w.replace(" ", "") in t for w in SKIP_WORDS):
        return 0.0
    t = t.removesuffix("원")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(만)?(?:(\d+)천)?", t)
    if not m:
        return None
    num = float(m.group(1))
    if m.group(2):
        value = num * 10_000 + (float(m.group(3)) * 1_000 if m.group(3) else 0.0)
    elif m.group(3):
        return None
    else:
        value = num * 10_000 if num < 10_000 else num
    return value
