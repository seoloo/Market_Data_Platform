"""개별 종목 뉴스 요약 — 정보 봇에 종목 이름을 보내면 답한다 (사용자 2026-10-10).

순수 함수만.

* 뉴스: 구글 뉴스 검색 RSS(키 없음, 최근 7 일)
  + 미국 종목은 야후 파이낸스 RSS(요약문 · 바로 가는 링크).
  제목 · 요약문 + (가져올 수 있으면) 기사 앞부분을 읽는다. 옵션 차트 같은 쓸모없는 항목은 거른다.
* AI 는 번호 붙인 기사만 근거로 3 ~ 5 줄 + 호재/악재 표시를 쓴다. 근거 번호가 없는 줄은 버린다.
  줄마다 근거 기사 링크를 단다(2026-10-11).
* **AI 의견**(강세 · 중립 · 약세)은 '검증 안 된 참고' 로만 보인다
  — 기록해 두고 20 거래일 뒤 채점한다
  (ops/stock_calls.py). 사용자 2026-10-11 요청.
* 가격 · 추세 상태 · 과거 비슷한 상태의 20 거래일 결과는 우리 계산(야후 일봉)이다.
  AI 가 쓰지 않는다.
  정보용 · 매매 신호 아님.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html import escape as html_escape
from typing import Any
from urllib.parse import quote

from auto_trading.ops.news_rss import NewsItem

__all__ = [
    "MAX_ITEMS",
    "Target",
    "TrendStats",
    "build_prompt",
    "format_reply",
    "gnews_url",
    "is_junk",
    "kr_index",
    "parse_answer",
    "pick_items",
    "price_block",
    "price_line",
    "resolve",
    "trend_stats",
    "yahoo_rss_url",
]

MAX_ITEMS = 15
MOODS = {"긍정": "🟢", "중립": "⚪", "부정": "🔴", "섞임": "🟡"}
TONES = {"호재": "🟢", "악재": "🔴", "중립": "⚪"}
VIEWS = {"강세": "📈", "중립": "➖", "약세": "📉"}
HORIZON = 20
"""과거 통계 · 채점의 앞 기간 (거래일)."""
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


def yahoo_rss_url(symbol: str) -> str:
    """야후 파이낸스 종목 뉴스 RSS (미국 기호) — 요약문과 바로 가는 링크가 있다."""
    return (
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(symbol)}&region=US&lang=en-US"
    )


_JUNK = re.compile(
    r"(interactive stock chart|stock chart \||\d{6}[CP]\d{8}"
    r"|stock price, news, quote|quote & history|price today|historical prices)",
    re.IGNORECASE,
)


def is_junk(item: NewsItem) -> bool:
    """기사가 아닌 항목 (옵션 차트 · 시세 페이지)."""
    return bool(_JUNK.search(item.title))


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
        if is_junk(it):
            continue
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


def build_prompt(
    target: Target, items: Sequence[NewsItem], excerpts: Mapping[int, str] | None = None
) -> tuple[str, str]:
    """``excerpts`` = 기사 번호(1 부터) → 본문 앞부분 (가져온 것만)."""
    excerpts = excerpts or {}
    system = (
        "너는 한국어 증권 뉴스 분석가다. "
        "아래 번호 붙은 기사(제목 · 요약 · 본문 일부)에만 근거해서 쓴다. "
        "기사에 없는 사실·숫자·전망을 지어내지 않는다. "
        "숫자(금액 · % · 날짜)가 기사에 있으면 살려 쓴다. "
        "반드시 JSON 하나만 답한다: "
        '{"one_line": "한 줄 결론(50자 안)", "mood": "긍정|중립|부정|섞임", '
        '"points": [{"text": "핵심 한두 문장(140자 안)", '
        '"tone": "호재|악재|중립", "ids": ["N1"]}], '
        '"view": "강세|중립|약세", '
        '"view_why": "그렇게 본 이유 한 문장(80자 안)", "view_ids": ["N2"]} '
        "points 는 중요한 순서로 3~5 개, ids 는 그 문장의 근거 기사 번호. "
        "같은 이야기는 한 point 로 묶는다. "
        "tone 은 그 내용이 이 종목 주주에게 좋은 소식인지(호재) 나쁜 소식인지(악재). "
        "view 는 기사 내용만 봤을 때 다음 한 달 주가에 대한 너의 의견이다 — "
        "확신이 없으면 중립. "
        "종목과 무관한 기사는 무시한다."
    )
    lines = [f"종목: {target.name}" + (f" ({target.code})" if target.code else ""), ""]
    for i, it in enumerate(items, 1):
        title, src = _split_title(it)
        lines.append(f"N{i}. [{src or it.source}] {title} ({it.published[:16]})")
        summ = _clean(it.summary)
        if summ and _norm(summ)[:40] != _norm(title)[:40]:
            lines.append(f"    요약: {summ[:300]}")
        if excerpts.get(i):
            lines.append(f"    본문 일부: {excerpts[i][:700]}")
    return system, "\n".join(lines)


def _clean(text: str) -> str:
    """RSS 요약의 HTML 태그 · 공백 정리."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def parse_answer(answer: Mapping[str, Any], n_items: int) -> dict[str, Any]:
    """AI 답 검사 — 근거 번호가 실제 기사인 줄만, 최대 5 개."""
    valid = {f"N{i}" for i in range(1, n_items + 1)}
    points = []
    for p in answer.get("points") or []:
        if not isinstance(p, Mapping):
            continue
        ids = [str(x) for x in p.get("ids") or [] if str(x) in valid]
        text = str(p.get("text") or "").strip()
        tone = str(p.get("tone") or "")
        if ids and text:
            points.append(
                {"text": text[:200], "ids": ids[:3], "tone": tone if tone in TONES else ""}
            )
    mood = str(answer.get("mood") or "")
    view = str(answer.get("view") or "")
    view_ids = [str(x) for x in answer.get("view_ids") or [] if str(x) in valid]
    return {
        "one_line": str(answer.get("one_line") or "").strip()[:80],
        "mood": mood if mood in MOODS else "",
        "points": points[:5],
        "dropped": max(0, len(answer.get("points") or []) - len(points)),
        "view": view if view in VIEWS and view_ids else "",
        "view_why": str(answer.get("view_why") or "").strip()[:120] if view_ids else "",
        "view_ids": view_ids[:3],
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


@dataclass(frozen=True, slots=True)
class TrendStats:
    """우리 추세 규칙(T/v2: 20 · 60 · 120 거래일 수익 부호 평균 > 0 = 켜짐)과 과거 통계."""

    on: bool
    score: float
    """부호 평균 (−1 ~ +1)."""
    n_same: int
    """과거에 지금과 같은 상태였던 날 수 (서로 겹친다 — 독립 표본은 약 n / 20)."""
    up_same: float
    mean_same: float
    n_all: int
    up_all: float
    mean_all: float


def _state(closes: Sequence[float], t: int) -> float:
    return sum(1 if closes[t] > closes[t - n] else -1 for n in (20, 60, 120)) / 3


def trend_stats(closes: Sequence[float], horizon: int = HORIZON) -> TrendStats | None:
    """오늘의 추세 상태와, 과거 같은 상태였던 날 뒤 ``horizon`` 거래일 수익 (순수, 미래 정보 없음).

    과거 날 t 의 상태는 t 까지의 종가로만 정하고, 결과는 t → t + horizon 종가다.
    오늘은 결과가 없으니 통계에 넣지 않는다. 자료가 짧으면(120 + horizon + 60 거래일 미만) None.
    """
    n = len(closes)
    if n < 120 + horizon + 60:
        return None
    today = _state(closes, n - 1)
    on = today > 0
    same: list[float] = []
    every: list[float] = []
    for t in range(120, n - horizon):
        r = closes[t + horizon] / closes[t] - 1
        every.append(r)
        if (_state(closes, t) > 0) == on:
            same.append(r)
    if not same:
        return None

    def up(xs: Sequence[float]) -> float:
        return sum(x > 0 for x in xs) / len(xs)

    return TrendStats(
        on,
        today,
        len(same),
        up(same),
        sum(same) / len(same),
        len(every),
        up(every),
        sum(every) / len(every),
    )


def price_block(closes: Sequence[float], currency: str) -> list[str]:
    """종가 · 1 달 · 3 달 · 1 년 수익 · 52 주 고점 대비 · 50 · 200 일 평균 위/아래 (우리 계산)."""
    if len(closes) < 2:
        return []
    last = closes[-1]

    def ret(n: int) -> str:
        return f"{last / closes[-1 - n] - 1:+.1%}" if len(closes) > n and closes[-1 - n] else "—"

    unit = "원" if currency == "KRW" else f" {currency}"
    price = f"{last:,.0f}{unit}" if currency == "KRW" else f"{last:,.2f}{unit}"
    out = [f"📊 {price} · 1달 {ret(21)} · 3달 {ret(63)} · 1년 {ret(252)}"]
    year = closes[-252:]
    high = max(year)
    marks = [f"52주 고점 대비 {last / high - 1:+.1%}"]
    for n in (50, 200):
        if len(closes) >= n:
            avg = sum(closes[-n:]) / n
            marks.append(f"{n}일 평균 " + ("위🔺" if last >= avg else "아래🔻"))
    out.append("   " + " · ".join(marks))
    return out


def _a(url: str, label: str) -> str:
    return f'<a href="{html_escape(url, quote=True)}">{html_escape(label)}</a>' if url else ""


def format_reply(
    target: Target,
    items: Sequence[NewsItem],
    judged: Mapping[str, Any] | None,
    price: Sequence[str],
    stats: TrendStats | None = None,
    record: str = "",
    laya: Mapping[str, tuple[str, float]] | None = None,
    laya_probs: Mapping[str, Mapping[str, float]] | None = None,
) -> str:
    """텔레그램 HTML 답 (``parse_mode=HTML``). 바깥 글은 모두 이스케이프한다.

    ``record`` = 지난 AI 의견 채점 한 줄 (있으면).
    ``laya`` = 기사 번호 → (호재 · 악재 · 중립, 확률) (사용자 2026-10-11 요청으로 표시).
    ``laya_probs`` = 기사 번호 → {호재 · 악재 · 중립: 확률}. 있으면 줄의 판단 옆에 그 판단에
    Laya 가 근거 기사들에서 준 확률의 평균을 붙인다(예: 🟢 호재 (62%))."""
    laya = laya or {}
    laya_probs = laya_probs or {}
    from auto_trading.notify.style import RULE, sub

    e = html_escape
    head = f"🗞 [종목 뉴스] {target.name}" + (
        f" ({target.code})" if target.code and target.code != target.name else ""
    )
    lines = [
        f"<b>{e(head)}</b>",
        e(sub(f"최근 7일 기사 {len(items)}건 · 제목·요약·본문 일부")),
        RULE,
    ]
    if not items:
        lines.append("최근 7일 기사를 찾지 못했어요. 이름을 정확히(예: 삼성전자, NVDA) 보내 보세요")
    elif not judged or not judged.get("points"):
        lines.append("요약을 만들지 못해 최근 제목만 보여 드려요")
        for it in items[:5]:
            title, src = _split_title(it)
            lines.append(
                f"• {e(title)}" + (f" ({e(src)})" if src else "") + f" {_a(it.link, '🔗')}"
            )
    else:
        mood = judged.get("mood") or ""
        if judged.get("one_line"):
            lines.append(f"🧭 <b>{e(judged['one_line'])}</b>")
        if mood:
            lines.append(f"분위기 {MOODS[mood]} {e(mood)}")
        nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        for k, p in enumerate(judged["points"]):
            tone = p.get("tone") or ""
            agree = [laya_probs[r][tone] for r in p["ids"] if tone in laya_probs.get(r, {})]
            pct = f" ({sum(agree) / len(agree):.0%})" if agree else ""
            mark = f"{TONES[tone]} {tone}{pct} · " if tone else ""
            lines += ["", f"{nums[k]} {mark}{e(p['text'])}"]
            refs = []
            for ref in p["ids"]:
                it = items[int(ref[1:]) - 1]
                title, src = _split_title(it)
                who = src or ("Yahoo" if it.source.startswith("yahoo") else "기사")
                ref_text = _a(it.link, f"{who}: {title[:40]}") or e(title[:40])
                if ref in laya:
                    label, prob = laya[ref]
                    ref_text += f" ({TONES[label]}{label} {prob:.0%})"
                refs.append(ref_text)
            lines.append("   📰 " + " · ".join(refs))
        if laya:
            count = {k: sum(1 for lab, _ in laya.values() if lab == k) for k in TONES}
            tally = " · ".join(f"{TONES[k]} {k} {n}" for k, n in count.items() if n)
            lines += ["", f"🧪 기사별 판단 (기사 {len(laya)}건): {tally}"]
        view = judged.get("view") or ""
        if view:
            lines += [
                "",
                f"🤖 AI 의견 {VIEWS[view]} <b>{e(view)}</b> — {e(judged.get('view_why') or '')}",
                e(sub("기사만 읽은 의견 · 검증 안 됨 · 기록해 두고 20거래일 뒤 채점해요")),
            ]
    lines += ["", RULE]
    lines += [e(x) for x in price]
    if stats is not None:
        state = "켜짐 🟢" if stats.on else "꺼짐 ⚪"
        lines += [
            f"📐 우리 추세 규칙(20·60·120일): <b>{state}</b> (점수 {stats.score:+.2f})",
            e(
                f"   과거 같은 상태 뒤 20거래일: 오른 비율 {stats.up_same:.0%} · "
                f"평균 {stats.mean_same:+.1%} ({stats.n_same:,}일)"
            ),
            e(
                f"   전체 기간 기준: 오른 비율 {stats.up_all:.0%} · 평균 {stats.mean_all:+.1%} "
                f"({stats.n_all:,}일)"
            ),
            e(
                sub(
                    "과거 통계라 앞으로를 보장하지 않아요 · "
                    "겹치는 기간이라 실제 독립 표본은 약 1/20"
                )
            ),
        ]
    if record:
        lines.append(e(record))
    lines.append("※ 정보용 · 매매 신호 아님 (가격 · 통계는 우리 계산)")
    return "\n".join(lines)
