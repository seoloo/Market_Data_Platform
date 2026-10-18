"""텔레그램 봇이 받은 글 읽기 (getUpdates, 웹훅 없음).

* 등록된 채팅(TELEGRAM_CHAT_ID)의 글만 돌려준다.

* 토큰은 주소에만 쓰고 출력 · 파일에 남기지 않는다(오류 문구에서도 가린다).
* ``offset`` 을 넘기면 그 앞 글은 텔레그램 서버에서 지워진다 — 같은 글을 두 번 처리하지 않는다.
* 받는 쪽이 여럿(Deneb 대화 · '현황' 봇)이라 :class:`InboxQueue` 한 파일로 나눠 읽는다(2026-10-10).
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
from typing import Any

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
"""'현황' 봇이 답하는 글. 나머지 글 · 버튼은 Deneb 대화 몫."""

HELP_WORDS = frozenset({"도움", "도움말", "사용법", "/help", "/start", "help"})
"""사용법 안내 (2026-10-11) — 봇 몫."""
LIQ_WORDS = frozenset({"정리", "/정리", "전부정리", "전부매도", "청산", "/liquidate"})
"""'전부 정리' 요청 (2026-10-11) — 미리 보기와 범위 버튼으로 답한다."""
UNHALT_WORDS = frozenset({"정지풀기", "/정지풀기", "/unhalt"})
PAPER_WORDS = frozenset({"paper", "/paper", "페이퍼", "/페이퍼", "가상장부"})
DEPOSIT_WORDS = frozenset({"입금확인", "/입금확인", "/deposit", "deposit"})
"""바이낸스 입금 바로 확인 · 분배 (2026-10-16) — '입금' 만은 Deneb 금액 답과 헷갈려 쓰지 않는다."""
"""Paper(가상 장부) 요약 (2026-10-15) — 봇 몫."""
LIQ_DATA = ("liq:", "unh:")
"""정리 · 정지 풀기 버튼의 callback_data 머리."""
STOCK_PREFIXES = ("개별주", "/개별주", "/stock", "비중", "/비중")
"""개별주 비중 추천 (2026-10-16) — '개별주 NVDA' 처럼 앞에 붙인다(종목만 보내면 Deneb 대화 몫)."""
STOCK_DATA = (
    "stk:",
    "stkr:",
    "stq:",
    "sko:",
    "stp:",
    "rcb:",
    "rcm:",
    "rcs:",
    "rsl:",
    "rsk:",
    "pol:",
)
"""개별주 버튼 머리 — 매수 · 손절 해제 · 수량 입력 · 수량 확인 · PEAD 알림 매수 ·
월간 추천(매수 · 더 보기 · 건너뛰기) · 매도 신호(매도 · 계속 보유) · 🧭 Polaris 회차(나무)."""


def _word(msg: InboxMessage) -> str:
    return msg.text.strip().lower().replace(" ", "")


def liquidation_symbol(msg: InboxMessage) -> str:
    """'정리 NVDA' · '청산 삼성전자' → 'NVDA' · '삼성전자' (한 종목 정리). 아니면 빈 값."""
    if msg.data:
        return ""
    parts = msg.text.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() in LIQ_WORDS:
        return parts[1].strip()
    return ""


def is_liquidation(msg: InboxMessage) -> bool:
    """'전부 정리' · '정리 종목' · '정지풀기' 글 또는 그 버튼."""
    if msg.data:
        return msg.data.startswith(LIQ_DATA)
    return _word(msg) in LIQ_WORDS | UNHALT_WORDS or bool(liquidation_symbol(msg))


STOCK_ASK_TTL_S = 300
"""봇이 '어떤 종목을 살까요?' 를 물은 뒤 이 시간 안의 답만 종목으로 본다."""


def stock_ask_path() -> Path:
    """'어떤 종목?' 질문 기록 — Deneb 대화(다른 실행기)도 같은 판단을 하도록 파일로 둔다."""
    return Path(__file__).resolve().parents[3] / "data" / "telegram" / "stock_ask.json"


def stock_ask(path: Path | None = None) -> tuple[int, int] | None:
    """(질문 메시지 번호, 물은 시각 유닉스 초). 없으면 None."""
    d = stock_ask_info(path)
    return (int(d["message_id"]), int(d["at"])) if d else None


def stock_ask_info(path: Path | None = None) -> dict[str, Any] | None:
    """질문 기록 전부 — ``kind`` 'symbol'(어떤 종목?) · 'qty'(몇 주?, ``symbol`` 함께)."""
    try:
        d = json.loads((path or stock_ask_path()).read_text(encoding="utf-8"))
        int(d["message_id"]), int(d["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return dict(d) if isinstance(d, dict) else None


def set_stock_ask(
    message_id: int,
    at_s: int,
    path: Path | None = None,
    *,
    kind: str = "symbol",
    symbol: str = "",
) -> None:
    p = path or stock_ask_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {"message_id": message_id, "at": at_s, "kind": kind, "symbol": symbol}
    p.write_text(json.dumps(d), encoding="utf-8")


def clear_stock_ask(path: Path | None = None) -> None:
    (path or stock_ask_path()).unlink(missing_ok=True)


def is_stock_answer(msg: InboxMessage, path: Path | None = None) -> bool:
    """'어떤 종목을 살까요?' 에 대한 답 (2026-10-16 사용자 '메뉴 누르면 … 내가 대답하는 식').

    그 질문에 답장했거나, 질문 뒤 5 분 안에 보낸 글 중 **금액이 아닌** 것 — 숫자 · '30만' · '쉬기'
    는 Deneb 금액 답이라 Deneb 몫으로 남긴다."""
    if msg.data:
        return False
    ask = stock_ask(path)
    if ask is None:
        return False
    mid, at = ask
    if not at <= msg.date <= at + STOCK_ASK_TTL_S:  # 시각 없는 글(0)은 답으로 안 봄
        return False
    if msg.reply_to:
        return msg.reply_to == mid
    if (stock_ask_info(path) or {}).get("kind") == "qty":
        # '몇 주?' 답은 숫자라 Deneb 금액과 헷갈린다 — 답장이거나 '5주' 처럼 '주' 를 붙였을 때만
        return re.fullmatch(r"\d{1,6}\s*주", msg.text.strip()) is not None
    t = msg.text.strip()
    others = COMMANDS | HELP_WORDS | PAPER_WORDS | LIQ_WORDS | UNHALT_WORDS
    return (
        0 < len(t) <= 30
        and parse_krw(t) is None
        and _word(msg) not in others
        and not any(w in t for w in SKIP_WORDS)
    )


def is_stock(msg: InboxMessage) -> bool:
    """'개별주 …' 글 · 그 버튼 · '어떤 종목?' 질문의 답."""
    if msg.data:
        return msg.data.startswith(STOCK_DATA)
    if is_stock_answer(msg):
        return True
    first = (msg.text.strip().split() or [""])[0].lower()
    return first in STOCK_PREFIXES or any(
        first.startswith(p) and first[len(p) :].isascii() and first[len(p) :].isalnum()
        for p in STOCK_PREFIXES
    )


ACC_PREFIXES = ("적립", "/적립", "자동적립", "/자동적립", "/acc")
"""자동 적립 종목 추가 · 빼기 (2026-10-18 사용자 '텔레그램에 자동적립 추가 및 삭제하는 메뉴')."""
ACC_DATA = ("acc:",)
ACC_ASK_TTL_S = 600


def acc_ask_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "telegram" / "acc_ask.json"


def acc_ask(path: Path | None = None) -> tuple[int, int] | None:
    """'추가할 종목?' 질문 (메시지 번호, 물은 시각 초). 없으면 None."""
    try:
        d = json.loads((path or acc_ask_path()).read_text(encoding="utf-8"))
        return int(d["message_id"]), int(d["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def set_acc_ask(
    message_id: int, at_s: int, path: Path | None = None, *, kind: str = "add", key: str = ""
) -> None:
    """``kind`` 'add'(추가할 종목?) · 'amt'(``key`` 종목의 새 하루 금액?)."""
    p = path or acc_ask_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {"message_id": message_id, "at": at_s, "kind": kind, "key": key}
    p.write_text(json.dumps(d), encoding="utf-8")


def acc_ask_info(path: Path | None = None) -> dict[str, Any]:
    """질문 기록 전부 (없으면 빈 dict) — kind · key."""
    try:
        d = json.loads((path or acc_ask_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(d) if isinstance(d, dict) else {}


def clear_acc_ask(path: Path | None = None) -> None:
    (path or acc_ask_path()).unlink(missing_ok=True)


def is_acc_answer(msg: InboxMessage, path: Path | None = None) -> bool:
    """'추가할 종목과 하루 금액?' 질문에 **답장**한 글 (10 분 안). 답장이 아니면 Deneb 금액과
    헷갈리므로 받지 않는다."""
    if msg.data or not msg.reply_to:
        return False
    ask = acc_ask(path)
    return (
        ask is not None and msg.reply_to == ask[0] and ask[1] <= msg.date <= ask[1] + ACC_ASK_TTL_S
    )


def is_acc(msg: InboxMessage) -> bool:
    """'적립' 글 · 그 버튼 · 추가 질문의 답장."""
    if msg.data:
        return msg.data.startswith(ACC_DATA)
    if is_acc_answer(msg):
        return True
    first = (msg.text.strip().split() or [""])[0].lower()
    return first in ACC_PREFIXES


def is_command(msg: InboxMessage) -> bool:
    """봇(scripts/telegram_bot.py) 몫 — 현황 · 정리 · 개별주 · 적립 류. 나머지는 Deneb 대화 몫."""
    return (
        is_liquidation(msg)
        or is_acc(msg)
        or is_stock(msg)
        or (not msg.data and _word(msg) in COMMANDS | HELP_WORDS | PAPER_WORDS | DEPOSIT_WORDS)
    )


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
