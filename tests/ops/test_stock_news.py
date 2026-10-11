"""종목 뉴스: 이름 찾기 · 근거 없는 줄 버리기 · 가격 줄은 우리 계산."""

from __future__ import annotations

from auto_trading.ops.news_rss import NewsItem
from auto_trading.ops.stock_news import (
    build_prompt,
    format_reply,
    gnews_url,
    is_junk,
    kr_index,
    parse_answer,
    pick_items,
    price_block,
    price_line,
    resolve,
    trend_stats,
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
    block = price_block(closes, "KRW")
    assert block[0].startswith("📊 110원") and "52주 고점 대비 +0.0%" in block[1]
    t = resolve("삼성전자", KR)
    judged = {
        "one_line": "요약 <b>",
        "mood": "섞임",
        "points": [{"text": "핵심 & 숫자", "ids": ["N1"], "tone": "호재"}],
        "view": "강세",
        "view_why": "수주",
    }
    item = NewsItem("gnews_ko", "반도체 호황 - 한경", "https://x.kr/a?b=1&c=2", "", "", "")
    text = format_reply(t, [item], judged, block, None, "🧾 기록")
    assert "<b>🗞 [종목 뉴스] 삼성전자 (005930)</b>" in text and "🟡 섞임" in text
    assert "요약 &lt;b&gt;" in text and "🟢 호재 · 핵심 &amp; 숫자" in text  # 바깥 글은 이스케이프
    assert '<a href="https://x.kr/a?b=1&amp;c=2">한경: 반도체 호황</a>' in text  # 근거 링크
    assert "🤖 AI 의견 📈 <b>강세</b> — 수주" in text and "검증 안 됨" in text
    assert "🧾 기록" in text and "정보용" in text
    empty = format_reply(t, [], None, [])
    assert "찾지 못했어요" in empty


def test_junk_items_are_dropped() -> None:
    junk = [
        _item("IREN261009C00038500 interactive stock chart | IREN Oct 2026 38.500 call stock"),
        _item("IREN Nov 2026 44.000 put (IREN261113P00044000) Interactive Stock Chart - Yahoo!"),
    ]
    real = _item("IREN shares sink 20% in ten days - Startup Fortune")
    assert all(is_junk(x) for x in junk) and not is_junk(real)
    assert pick_items([*junk, real]) == [real]


def test_parse_answer_view_needs_evidence_and_tone_is_checked() -> None:
    got = parse_answer(
        {
            "points": [{"text": "a", "ids": ["N1"], "tone": "대박"}],
            "view": "강세",
            "view_why": "이유",
            "view_ids": ["N7"],
        },
        2,
    )
    assert got["points"][0]["tone"] == "" and got["view"] == "" and got["view_why"] == ""
    ok = parse_answer({"view": "약세", "view_why": "w", "view_ids": ["N2"]}, 2)
    assert ok["view"] == "약세" and ok["view_ids"] == ["N2"]


def test_prompt_carries_summary_and_excerpt() -> None:
    t = resolve("NVDA", KR)
    it = NewsItem("yahoo_en", "Nvidia beats", "u", "", "<p>Revenue rose 50% to $30B</p>", "")
    _system, user = build_prompt(t, [it], {1: "Full text start"})
    assert "요약: Revenue rose 50% to $30B" in user and "본문 일부: Full text start" in user


def test_trend_stats_uses_only_the_past() -> None:
    up = [100.0 * 1.001**k for k in range(400)]
    s = trend_stats(up)
    assert s is not None and s.on and s.score == 1.0
    assert s.n_all == 400 - 120 - 20 and s.up_same == 1.0
    # 오늘 이후 자료가 없으니 결과 창은 끝에서 20 일 앞까지만 — 마지막 값만 바꿔도 통계는 같다
    bumped = trend_stats([*up[:-1], up[-1] * 1.5])
    assert bumped is not None and (bumped.n_all, bumped.up_all) == (s.n_all, s.up_all)
    crashed = trend_stats([*up[:-1], up[-1] * 0.5])  # 오늘 꺼짐 — 과거에 꺼진 날이 없으면 없음
    assert crashed is None
    assert trend_stats(up[:150]) is None


def test_trend_stats_off_state_compares_same_state_days() -> None:
    wave = [100.0 + 30 * ((k // 150) % 2 == 0) - 0.05 * k for k in range(900)]
    s = trend_stats(wave)
    assert s is not None and s.n_same < s.n_all and 0.0 <= s.up_same <= 1.0
