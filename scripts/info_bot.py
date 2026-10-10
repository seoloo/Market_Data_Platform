"""정보 봇 — 종목 이름을 보내면 최근 7 일 뉴스를 요약해 답한다 (사용자 2026-10-10).

사용 예::

    python -m scripts.info_bot run           # 받은 글에 답 (예약: 1 분마다)
    python -m scripts.info_bot ask 삼성전자   # 보내지 않고 화면에만

* ``TELEGRAM_INFO_BOT_TOKEN`` 의 봇만 읽는다. 없으면 아무것도 하지 않는다 —
  투자 봇의 글을 가져가면 Glide 금액 · 버튼 답이 사라진다.
* 뉴스: 구글 뉴스 검색 RSS(키 없음). 요약: Gemini (주간 테마와 같은 키). 가격 줄: 야후 일봉.
* 주문 · 계좌 조회 없음. 정보용.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import (
    TelegramCredentials,
    load_gemini_key,
    read_env_file,
)
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.notify.inbox import InboxQueue, TelegramInbox
from auto_trading.ops.news_rss import NewsItem, parse_feed
from auto_trading.ops.stock_news import (
    Target,
    build_prompt,
    format_reply,
    gnews_url,
    kr_index,
    parse_answer,
    pick_items,
    price_line,
    resolve,
)

REPO = Path(__file__).resolve().parent.parent
PER_RUN = 3
"""한 번 실행에 답하는 글 수 (나머지는 다음 실행)."""
HELP = (
    "🪐 정보 봇\n"
    "종목 이름이나 기호를 보내면 최근 7일 뉴스를 요약해 드려요.\n"
    "예: 삼성전자 · 005930 · 엔비디아 · NVDA · TQQQ\n"
    "주간 테마(토 09:00)와 시장 브리핑도 여기로 와요."
)
_UA = {"User-Agent": "Mozilla/5.0"}


def info_credentials(root: Path = REPO) -> TelegramCredentials | None:
    """정보 봇 토큰이 따로 있을 때만 (투자 봇으로 돌아가지 않는다)."""
    env = read_env_file(root / ".env")
    token, chat = env.get("TELEGRAM_INFO_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    return TelegramCredentials(token, chat) if token and chat else None


def _kr(root: Path) -> dict[str, tuple[str, str, str]]:
    infos = []
    for rel in ("data/kr_shadow/info.json", "data/research/kq_stocks/info.json"):
        try:
            infos.append(json.loads((root / rel).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return kr_index(infos)


def fetch_news(target: Target) -> list[NewsItem]:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    items: list[NewsItem] = []
    for query, lang in target.queries:
        try:
            r = httpx.get(gnews_url(query, lang), headers=_UA, timeout=15, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            items += parse_feed(r.text, f"gnews_{lang}", now)
    return pick_items(items)


def fetch_price(target: Target) -> str:
    if not target.yahoo:
        return ""
    try:
        res = httpx.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{target.yahoo}"
            "?range=6mo&interval=1d",
            headers=_UA,
            timeout=10,
        ).json()["chart"]["result"][0]
        closes = [float(c) for c in res["indicators"]["quote"][0]["close"] if c]
        currency = str(res["meta"].get("currency") or "USD")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return ""
    return price_line(closes, currency)


def _summarize(target: Target, items: list[NewsItem], root: Path) -> dict[str, Any] | None:
    key = load_gemini_key(root / ".env")
    if key is None or not items:
        return None
    from scripts.market_brief import ask_gemini

    system, user = build_prompt(target, items)
    answer, _attempts = ask_gemini(key.value, system, user)
    return parse_answer(answer, len(items)) if isinstance(answer, dict) else None


def answer(text: str, root: Path = REPO) -> str:
    t = text.strip()
    if t.lower() in ("/start", "/help", "도움", "도움말", "help", "?"):
        return HELP
    if len(t) > 30:
        return "종목 이름이나 기호만 보내 주세요 (예: 삼성전자, NVDA)"
    target = resolve(t.removeprefix("뉴스").strip() or t, _kr(root))
    items = fetch_news(target)
    return format_reply(target, items, _summarize(target, items, root), fetch_price(target))


def run(root: Path = REPO) -> int:
    creds = info_credentials(root)
    if creds is None:
        print("정보 봇 토큰 없음 — 할 일 없음")
        return 0
    try:
        msgs = InboxQueue(root / "data" / "telegram" / "info_inbox.json").take(
            TelegramInbox(creds), lambda m: True
        )
    except NotifyError as exc:
        print(f"텔레그램 받기 실패: {exc}")
        return 1
    asks = [m.text for m in msgs if m.text][-PER_RUN:]
    if not asks:
        print("정보 봇: 받은 글 없음")
        return 0
    bot = TelegramNotifier(creds)
    try:
        for text in asks:
            if text.strip().lower() not in ("/start", "/help", "도움", "도움말", "help", "?"):
                bot.send(f"🔎 '{text.strip()[:30]}' 뉴스 찾는 중…")
            reply = answer(text, root)
            bot.send(reply)
            print(reply)
    except NotifyError as exc:
        print(f"텔레그램 보내기 실패: {exc}")
        return 1
    finally:
        bot.close()
    return 0


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        return run()
    if len(argv) >= 2 and argv[0] == "ask":
        sys.stdout.write(answer(" ".join(argv[1:])) + "\n")
        return 0
    raise SystemExit("usage: python -m scripts.info_bot run | ask <종목>")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
