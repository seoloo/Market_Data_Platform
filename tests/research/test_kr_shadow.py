from __future__ import annotations

from urllib.parse import unquote

from scripts.kr_shadow import KR_FEEDS, merge_csv

from auto_trading.ops.news_ai import parse_reply
from auto_trading.ops.news_ai_kr import SYSTEM, build_messages


def test_merge_csv_appends_and_overwrites_same_day_sorted() -> None:
    text = "date,close\n20260901,10\n20260902,11\n"
    out = merge_csv(text, [("20260903", "12"), ("20260902", "11.5")])
    assert out == "date,close\n20260901,10\n20260902,11.5\n20260903,12\n"
    assert merge_csv("date,close\n", [("20260901", "1")]) == "date,close\n20260901,1\n"


def test_korean_news_feeds_are_encoded_google_news_searches() -> None:
    assert all(u.startswith("https://news.google.com/rss/search?q=") for u in KR_FEEDS.values())
    assert all("hl=ko&gl=KR&ceid=KR:ko" in u for u in KR_FEEDS.values())
    assert "주도주" in unquote(KR_FEEDS["gnews_kr_leaders"])
    assert all(" " not in u for u in KR_FEEDS.values())


def test_prompt_gives_only_news_and_universe_not_mechanical_lists() -> None:
    items = [
        {"source": "gnews_kr_leaders", "title": "반도체 강세", "summary": "", "fetched_at": "T2"},
        {
            "source": "gnews_kr_flows",
            "title": "외국인 순매수",
            "summary": "x" * 500,
            "fetched_at": "T1",
        },
    ]
    msgs = build_messages("2026-10-02", items, {"005930": "삼성전자", "000660": "SK하이닉스"})
    assert msgs[0]["content"] == SYSTEM
    user = msgs[1]["content"]
    assert "뉴스 수집 시각: T2" in user and "2026-10-02" in user
    assert "000660 SK하이닉스 · 005930 삼성전자" in user
    assert "x" * 121 not in user  # 요약은 120 자까지
    assert "score" not in user and "주도 점수" not in user


def test_reply_codes_outside_universe_are_set_aside() -> None:
    reply = (
        '{"leading_sectors": ["반도체", "조선"], "market_room": "up", "confidence": 0.7,'
        ' "leaders": [{"symbol": "005930", "reason": "HBM"},'
        ' {"symbol": "247540", "reason": "코스닥"}],'
        ' "risks": [], "summary": "요약"}'
    )
    got = parse_reply(reply, ["005930", "000660"])
    assert [x["symbol"] for x in got["leaders"]] == ["005930"]
    assert got["outside_universe"] == ["247540"]
