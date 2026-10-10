"""개별 종목 뉴스 요약 — 정보 봇에 종목 이름을 보내면 답한다 (사용자 2026-10-10). 순수 함수만.

* 뉴스: 구글 뉴스 검색 RSS(키 없음, 최근 7 일). 기사 **제목 · 짧은 요약문**만 읽는다(본문 아님).
* AI 는 번호 붙인 기사만 근거로 3 ~ 5 줄을 쓴다.
  근거 번호가 없는 줄은 버린다(주간 테마와 같은 원칙).
* 가격 한 줄은 우리 계산(야후 일봉)이다. AI 가 쓰지 않는다. 정보용 · 매매 신호 아님.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

from auto_trading.ops.news_rss import NewsItem

__all__ = [
    "MAX_ITEMS",
    "Target",
    "build_prompt",
    "format_reply",
    "gnews_url",
    "kr_index",
    "parse_answer",
    "pick_items",
    "price_line",
    "resolve",
]

MAX_ITEMS = 15
MOODS = {"긍정": "🟢", "중립": "⚪", "부정": "🔴", "섞임": "🟡"}
US_ALIASES: Mapping[str, str] = {
    "엔비디아": "NVDA",
    "테슬라": "TSLA",
    "애플": "AAPL",
    "마이크로소프트": "MSFT",
    "마소": "MSFT",
    "구글": "GOOGL",
    "알파벳": "GOOGL",
    "아마존": "AMZN",
    "메타": "META",
    "넷플릭스": "NFLX",
    "팔란티어": "PLTR",
    "브로드컴": "AVGO",
    "에이엠디": "AMD",
    "인텔": "INTC",
    "티에스엠씨": "TSM",
    "아이온큐": "IONQ",
    "퀀텀스케이프": "QS",
    "코인베이스": "COIN",
    "마이크로스트래티지": "MSTR",
    "스트래티지": "MSTR",
    "비트코인": "BTC-USD",
    "이더리움": "ETH-USD",
    "솔라나": "SOL-USD",
}
"""자주 쓰는 한글 이름 → 미국 기호. 없는 이름은 그 글자 그대로 검색만 한다."""
_TICKER = re.compile(r"[A-Za-z][A-Za-z.\-]{0,5}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


@dataclass(frozen=True, slots=True)
class Target:
    name: str
    code: str | None
    """국내 6 자리 · 미국 기호 · 모르면 None."""
    yahoo: str | None
    """가격 한 줄을 낼 야후 기호 (모르면 None)."""
    queries: tuple[tuple[str, str], ...]
    """(검색어, 'ko' | 'en')."""


def kr_index(infos: Iterable[Mapping[str, Mapping[str, Any]]]) -> dict[str, tuple[str, str, str]]:
    """info.json 들(코드 → 정보) → 정규화한 이름 → (코드, 이름, 시장). 상장폐지는 뺀다."""
    out: dict[str, tuple[str, str, str]] = {}
    for info in infos:
        for code, row in info.items():
            name = str(row.get("name") or "")
            if not name or row.get("abol"):
                continue
            market = str(row.get("market") or row.get("market_now") or "")
            out.setdefault(_norm(name), (str(code), name, market))
    return out


def resolve(text: str, kr: Mapping[str, tuple[str, str, str]]) -> Target:
    """보낸 글 → 검색 대상. 국내 이름 · 국내 코드 · 한글 별명 · 미국 기호 · 그 밖 (그대로 검색)."""
    t = text.strip()
    key = _norm(t)
    by_code = {v[0]: v for v in kr.values()}
    hit = kr.get(key) or by_code.get(t)
    if hit:
        code, name, market = hit
        return Target(name, code, f"{code}.{'KQ' if market == 'KSQ' else 'KS'}", ((name, "ko"),))
    if key in US_ALIASES:
        sym = US_ALIASES[key]
        base = sym.split("-")[0]
        return Target(t, sym, sym, ((t, "ko"), (f"{base} stock", "en")))
    if _TICKER.fullmatch(t):
        sym = t.upper()
        return Target(sym, sym, sym, ((f"{sym} 주가", "ko"), (f"{sym} stock", "en")))
    return Target(t, None, None, ((t, "ko"),))


def gnews_url(query: str, lang: str) -> str:
    loc = "hl=ko&gl=KR&ceid=KR:ko" if lang == "ko" else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote(query + ' when:7d')}&{loc}"


def _when(item: NewsItem) -> float:
    try:
        return parsedate_to_datetime(item.published).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0.0


def pick_items(items: Sequence[NewsItem], limit: int = MAX_ITEMS) -> list[NewsItem]:
    """제목이 같은 기사(언론사만 다름)는 하나만, 최근 것부터 ``limit`` 개."""
    seen: set[str] = set()
    out: list[NewsItem] = []
    for it in sorted(items, key=_when, reverse=True):
        head = _norm(it.title.rsplit(" - ", 1)[0])
        if head in seen:
            continue
        seen.add(head)
        out.append(it)
    return out[:limit]


def _split_title(item: NewsItem) -> tuple[str, str]:
    """구글 뉴스 제목 '제목 - 언론사' → (제목, 언론사)."""
    head, _, src = item.title.rpartition(" - ")
    return (head, src) if head else (item.title, "")


def build_prompt(target: Target, items: Sequence[NewsItem]) -> tuple[str, str]:
    system = (
        "너는 한국어 증권 뉴스 요약가다. 아래 번호 붙은 기사 제목·요약문에만 근거해서 쓴다. "
        "기사에 없는 사실·숫자·전망을 지어내지 않는다. 매수·매도를 권하지 않는다. "
        "반드시 JSON 하나만 답한다: "
        '{"one_line": "한 줄 결론(40자 안)", "mood": "긍정|중립|부정|섞임", '
        '"points": [{"text": "핵심 한 문장(80자 안)", "ids": ["N1", "N3"]}]} '
        "points 는 중요한 순서로 3~5 개, ids 는 그 문장의 근거 기사 번호. "
        "같은 이야기는 한 point 로 묶는다. 종목과 무관한 기사는 무시한다."
    )
    lines = [f"종목: {target.name}" + (f" ({target.code})" if target.code else ""), ""]
    for i, it in enumerate(items, 1):
        title, src = _split_title(it)
        lines.append(f"N{i}. [{src or it.source}] {title} ({it.published[:16]})")
    return system, "\n".join(lines)


def parse_answer(answer: Mapping[str, Any], n_items: int) -> dict[str, Any]:
    """AI 답 검사 — 근거 번호가 실제 기사인 줄만, 최대 5 개."""
    valid = {f"N{i}" for i in range(1, n_items + 1)}
    points = []
    for p in answer.get("points") or []:
        if not isinstance(p, Mapping):
            continue
        ids = [str(x) for x in p.get("ids") or [] if str(x) in valid]
        text = str(p.get("text") or "").strip()
        if ids and text:
            points.append({"text": text[:120], "ids": ids[:3]})
    mood = str(answer.get("mood") or "")
    return {
        "one_line": str(answer.get("one_line") or "").strip()[:60],
        "mood": mood if mood in MOODS else "",
        "points": points[:5],
        "dropped": max(0, len(answer.get("points") or []) - len(points)),
    }


def price_line(closes: Sequence[float], currency: str) -> str:
    """종가 · 1 달(21 거래일) · 3 달(63) 수익 · 50 일 평균 위/아래 (우리 계산)."""
    if len(closes) < 2:
        return ""
    last = closes[-1]

    def ret(n: int) -> str:
        return f"{last / closes[-1 - n] - 1:+.1%}" if len(closes) > n and closes[-1 - n] else "—"

    unit = "원" if currency == "KRW" else f" {currency}"
    price = f"{last:,.0f}{unit}" if currency == "KRW" else f"{last:,.2f}{unit}"
    trend = ""
    if len(closes) >= 50:
        avg = sum(closes[-50:]) / 50
        trend = " · 50일 평균 " + ("위 🔺" if last >= avg else "아래 🔻")
    return f"📊 {price} · 1달 {ret(21)} · 3달 {ret(63)}{trend}"


def format_reply(
    target: Target,
    items: Sequence[NewsItem],
    judged: Mapping[str, Any] | None,
    price: str,
) -> str:
    from auto_trading.notify.style import RULE, sub

    head = f"🗞 [종목 뉴스] {target.name}" + (
        f" ({target.code})" if target.code and target.code != target.name else ""
    )
    lines = [head, sub(f"최근 7일 기사 {len(items)}건 · 제목·요약만 읽음"), RULE]
    if not items:
        lines.append("최근 7일 기사를 찾지 못했어요. 이름을 정확히(예: 삼성전자, NVDA) 보내 보세요")
    elif not judged or not judged.get("points"):
        lines.append("요약을 만들지 못해 최근 제목만 보여 드려요")
        for it in items[:5]:
            title, src = _split_title(it)
            lines.append(f"• {title}" + (f" ({src})" if src else ""))
    else:
        mood = judged.get("mood") or ""
        if judged.get("one_line"):
            lines.append(f"🧭 {judged['one_line']}")
        if mood:
            lines.append(f"분위기 {MOODS[mood]} {mood}")
        nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        for k, p in enumerate(judged["points"]):
            lines += ["", f"{nums[k]} {p['text']}"]
            it = items[int(p["ids"][0][1:]) - 1]
            title, src = _split_title(it)
            lines.append(sub(f"📰 {src + ' · ' if src else ''}{title[:60]}"))
    lines += ["", RULE]
    if price:
        lines.append(price + " (우리 계산)")
    lines.append("※ 정보용 · 매매 신호 아님")
    return "\n".join(lines)
