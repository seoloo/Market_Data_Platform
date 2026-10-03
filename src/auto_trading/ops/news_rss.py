"""무료 RSS·Atom 뉴스 제목 모으기 (문서 115 2 단계 준비) — 파싱은 순수 함수.

가입·키가 필요 없는 공개 주소만 쓴다. 제목·요약·링크·발행 시각과 **모은 시각**을 남긴다
(나중에 AI 판단을 붙일 때 그 시각 이전 뉴스만 쓰게 하려는 것).
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any

FEEDS: dict[str, str] = {
    "fed_press": "https://www.federalreserve.gov/feeds/press_all.xml",
    "cnbc_markets": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "marketwatch_top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "gnews_stock_market": "https://news.google.com/rss/search?q=stock+market&hl=en-US&gl=US&ceid=US:en",
    "gnews_fed": "https://news.google.com/rss/search?q=Federal+Reserve&hl=en-US&gl=US&ceid=US:en",
    "gnews_sector_leaders": "https://news.google.com/rss/search?q=leading+sectors+stocks&hl=en-US&gl=US&ceid=US:en",
}
"""고정 출처 (2026-10-03 접속 확인). SEC 공시는 연락처 머리말이 필요해 뺐다."""
YAHOO_TICKER = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
MAX_SUMMARY = 400
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class NewsItem:
    source: str
    title: str
    link: str
    published: str
    summary: str
    fetched_at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    raw = "".join(el.itertext())
    return html.unescape(_TAG.sub("", raw)).strip()


def _first(fields: dict[str, ET.Element], *names: str) -> ET.Element | None:
    for name in names:
        el = fields.get(name)
        if el is not None:
            return el
    return None


def parse_feed(xml_text: str, source: str, fetched_at: str) -> list[NewsItem]:
    """RSS 2.0 의 item 과 Atom 의 entry 를 모두 읽는다. 깨진 XML 이면 빈 목록."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[NewsItem] = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind not in ("item", "entry"):
            continue
        fields: dict[str, ET.Element] = {}
        for child in el:
            fields.setdefault(_local(child.tag), child)
        title = _text(fields.get("title"))
        if not title:
            continue
        link_el = fields.get("link")
        link = ""
        if link_el is not None:
            link = (link_el.get("href") or _text(link_el)).strip()
        # ET.Element 은 자식이 없으면 거짓이라 ``or`` 로 고르면 안 된다
        published = _text(_first(fields, "pubDate", "published", "updated"))
        summary = _text(_first(fields, "description", "summary"))[:MAX_SUMMARY]
        out.append(NewsItem(source, title, link, published, summary, fetched_at))
    return out


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """링크(없으면 제목) 기준으로 처음 것만 남긴다."""
    seen: set[str] = set()
    out = []
    for it in items:
        key = it.link or it.title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
