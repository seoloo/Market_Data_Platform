"""종목 뉴스: 이름 찾기 · 근거 없는 줄 버리기 · 가격 줄은 우리 계산."""

from __future__ import annotations

from auto_trading.ops.news_rss import NewsItem
from auto_trading.ops.stock_news import (
    format_reply,
    gnews_url,
    kr_index,
    parse_answer,
    pick_items,
    price_line,
    resolve,
)

KR = kr_index(
    [
        {
            "005930": {"name": "삼성전자", "market": "STK"},
            "000030": {"name": "우리은행", "market": "STK", "abol": "20190213"},
        },
        {"247540": {"name": "에코프로비엠", "market_now": "KSQ"}},
    ]
)


def _item(title: str, when: str = "Fri, 09 Oct 2026 10:00:00 GMT") -> NewsItem:
    return NewsItem("gnews_ko", title, "", when, "", "")


def test_resolve_kr_us_and_unknown() -> None:
    t = resolve(" 삼성 전자 ", KR)
    assert (t.code, t.yahoo) == ("005930", "005930.KS")
    assert resolve("247540", KR).yahoo == "247540.KQ"
    assert resolve("우리은행", KR).code is None  # 상장폐지는 빼고 그대로 검색만
    nv = resolve("엔비디아", KR)
    assert nv.yahoo == "NVDA" and ("NVDA stock", "en") in nv.queries
    assert resolve("tqqq", KR).yahoo == "TQQQ"


def test_gnews_url_is_last_7_days_and_encoded() -> None:
    url = gnews_url("삼성전자", "ko")
    assert "when%3A7d" in url and "hl=ko" in url and "삼성" not in url


def test_pick_items_drops_same_title_and_sorts_recent() -> None:
    items = [
        _item("실적 발표 - 한경", "Thu, 08 Oct 2026 10:00:00 GMT"),
        _item("실적 발표 - 매경", "Fri, 09 Oct 2026 10:00:00 GMT"),
        _item("신제품 - YTN", "Fri, 09 Oct 2026 12:00:00 GMT"),
    ]
    assert [x.title for x in pick_items(items)] == ["신제품 - YTN", "실적 발표 - 매경"]


def test_parse_answer_keeps_only_cited_points() -> None:
    got = parse_answer(
        {
            "one_line": "좋음",
            "mood": "긍정",
            "points": [
                {"text": "근거 있음", "ids": ["N1"]},
                {"text": "근거 없음", "ids": ["N9"]},
                {"text": "", "ids": ["N2"]},
            ],
        },
        2,
    )
    assert [p["text"] for p in got["points"]] == ["근거 있음"] and got["dropped"] == 2
    assert parse_answer({"mood": "대박"}, 1)["mood"] == ""


def test_price_line_and_reply() -> None:
    closes = [100.0] * 63 + [110.0]
    line = price_line(closes, "KRW")
    assert line.startswith("📊 110원") and "1달 +10.0%" in line and "50일 평균 위" in line
    t = resolve("삼성전자", KR)
    judged = {"one_line": "요약", "mood": "섞임", "points": [{"text": "핵심", "ids": ["N1"]}]}
    text = format_reply(t, [_item("반도체 호황 - 한경")], judged, line)
    assert "🗞 [종목 뉴스] 삼성전자 (005930)" in text and "🟡 섞임" in text
    assert "📰 한경 · 반도체 호황" in text and "정보용" in text
    empty = format_reply(t, [], None, "")
    assert "찾지 못했어요" in empty
