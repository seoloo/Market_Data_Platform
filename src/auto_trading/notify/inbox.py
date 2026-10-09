"""텔레그램 봇이 받은 글 읽기 (getUpdates, 웹훅 없음).

* 등록된 채팅(TELEGRAM_CHAT_ID)의 글만 돌려준다.

* 토큰은 주소에만 쓰고 출력 · 파일에 남기지 않는다(오류 문구에서도 가린다).
* ``offset`` 을 넘기면 그 앞 글은 텔레그램 서버에서 지워진다 — 같은 글을 두 번 처리하지 않는다.
* 받는 쪽이 여럿(Glide 대화 · '현황' 봇)이라 :class:`InboxQueue` 한 파일로 나눠 읽는다(2026-10-10).
  직접 ``fetch`` 를 부르는 쪽이 둘이면 서로의 글 · 버튼을 지운다.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from auto_trading.config.secrets import TelegramCredentials
from auto_trading.notify.channels import NotifyError


@dataclass(frozen=True, slots=True)
class InboxMessage:
    update_id: int
    date: int
    """유닉스 초."""
    text: str
    data: str = ""
    """인라인 버튼을 눌렀을 때의 callback_data (글이면 빈 값)."""
    callback_id: str = ""
    reply_to: int = 0
    """답장한 원래 메시지 번호 (없으면 0)."""
    message_id: int = 0


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
            cq = upd.get("callback_query")
            if cq:
                cmsg = cq.get("message") or {}
                chat = str((cmsg.get("chat") or {}).get("id", ""))
                data = cq.get("data")
                if chat == str(self._creds.chat_id) and isinstance(data, str):
                    out.append(
                        InboxMessage(
                            uid,
                            int(cmsg.get("date", 0)),
                            "",
                            data=data,
                            callback_id=str(cq.get("id", "")),
                            message_id=int(cmsg.get("message_id", 0)),
                        )
                    )
                continue
            msg = upd.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text")
            if chat == str(self._creds.chat_id) and isinstance(text, str):
                reply = msg.get("reply_to_message") or {}
                out.append(
                    InboxMessage(
                        uid,
                        int(msg.get("date", 0)),
                        text.strip(),
                        reply_to=int(reply.get("message_id", 0)),
                        message_id=int(msg.get("message_id", 0)),
                    )
                )
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


COMMANDS = frozenset({"현황", "/현황", "수익", "/수익", "잔고", "/status", "status"})
"""'현황' 봇이 답하는 글. 나머지 글 · 버튼은 Glide 대화 몫."""


def is_command(msg: InboxMessage) -> bool:
    return not msg.data and msg.text.strip().lower().replace(" ", "") in COMMANDS


KEEP_S = 86_400
"""대기열에서 아무도 안 가져간 글은 하루 뒤 버린다 (텔레그램 서버 보관 기간과 같게)."""
_LOCK_WAIT_S = 15.0
_LOCK_STALE_S = 60.0


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock = path.with_suffix(".lock")
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_S:
                    lock.unlink(missing_ok=True)  # 죽은 실행이 남긴 잠금
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise NotifyError("받은 글 대기열 잠금 시간 초과") from None
            time.sleep(0.2)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


class InboxQueue:
    """봇이 받은 글을 여러 실행기가 나눠 가져가는 대기열 (파일 하나 + 잠금).

    ``take`` 는 잠근 채로 ① 텔레그램에서 새 글을 받아 쌓고 ② ``want`` 에 맞는 글만 꺼내 돌려준다.
    나머지는 다른 실행기를 위해 남긴다. ``start_offset`` 은 파일이 처음 생길 때만 쓴다.
    """

    def __init__(self, path: Path, start_offset: int = 0) -> None:
        self.path = path
        self.start_offset = start_offset

    def _load(self) -> dict[str, object]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"offset": self.start_offset, "pending": []}
        return d if isinstance(d, dict) else {"offset": self.start_offset, "pending": []}

    def take(
        self, inbox: TelegramInbox, want: Callable[[InboxMessage], bool]
    ) -> list[InboxMessage]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _locked(self.path):
            d = self._load()
            offset = int(str(d.get("offset", self.start_offset)))
            raw = d.get("pending")
            pending = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
            msgs, nxt = inbox.fetch(offset)  # 실패하면 NotifyError — 파일은 그대로
            now = time.time()
            pending += [{"at": now, "msg": asdict(m)} for m in msgs]
            out: list[InboxMessage] = []
            keep: list[dict[str, object]] = []
            for p in pending:
                m = InboxMessage(**p["msg"])
                if want(m):
                    out.append(m)
                elif now - float(p.get("at", now)) < KEEP_S:
                    keep.append(p)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"offset": nxt, "pending": keep}), encoding="utf-8")
            tmp.replace(self.path)
        return out
