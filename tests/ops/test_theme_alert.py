from __future__ import annotations

import json

import pytest

from auto_trading.ops.theme_alert import (
    build_messages,
    diff_weeks,
    format_market,
    number_headlines,
    parse_weekly,
)

EVIL = "<script>alert(1)</script>"


def _heads() -> dict[str, list[dict[str, str]]]:
    kr = number_headlines(
        [
            {"title": "전력기기 수출 사상 최대 - 한경", "link": "a", "fetched_at": "2"},
            {
                "title": "전력기기 수출 사상 최대 - 매경",
                "link": "b",
                "fetched_at": "1",
            },  # 같은 제목
            {"title": "조선 수주 호조", "link": "c", "fetched_at": "3"},
        ],
        "K",
    )
    return {"kr": kr, "us": number_headlines([{"title": "AI capex surges", "link": "u"}], "U")}


def test_headlines_are_numbered_newest_first_and_deduped() -> None:
    kr = _heads()["kr"]
    assert [h["id"] for h in kr] == ["K1", "K2"]
    assert kr[0]["title"] == "조선 수주 호조" and kr[1]["link"] == "a"


def test_prompt_has_only_headlines_and_universe() -> None:
    msgs = build_messages("2026-10-10", _heads(), {"kr": {"267260": "HD현대일렉트릭"}, "us": {}})
    user = msgs[1]["content"]
    assert "[K1] 조선 수주 호조" in user and "267260:HD현대일렉트릭" in user
    assert "추세" not in user and "주도주 목록" not in user  # 우리 계산 결과는 주지 않는다
    assert "뉴스 번호" in msgs[0]["content"]


def test_parse_drops_unknown_codes_and_unsupported_claims() -> None:
    heads = _heads()
    uni = {"kr": {"267260": "HD현대일렉트릭"}, "us": {"NVDA": "NVDA"}}
    reply = {
        "kr": {
            "themes": [
                {"name": "전력기기", "why": "수출", "evidence": ["K2", "K99"], "confidence": 1.7},
                {"name": "근거 없음", "why": "느낌", "evidence": []},
            ],
            "picks": [
                {"symbol": "267260", "theme": "전력기기", "why": "대표", "evidence": ["K2"]},
                {"symbol": "999999", "theme": "?", "why": "?", "evidence": ["K1"]},
                {"symbol": "267260", "theme": "x", "why": "번호 없음", "evidence": ["U1"]},
            ],
            "risks": ["이미 많이 오름"],
        },
        "us": {
            "themes": [],
            "picks": [{"symbol": "NVDA", "theme": "AI", "why": EVIL, "evidence": ["U1"]}],
        },
        "summary": "요약",
    }
    out = parse_weekly(json.dumps(reply, ensure_ascii=False), uni, heads)
    assert [t["name"] for t in out["kr"]["themes"]] == ["전력기기"]
    assert (
        out["kr"]["themes"][0]["evidence"] == ["K2"] and out["kr"]["themes"][0]["confidence"] == 1.0
    )
    assert [p["symbol"] for p in out["kr"]["picks"]] == ["267260"]
    assert out["dropped"] == 3
    with pytest.raises(ValueError):
        parse_weekly("not json", uni, heads)


def test_message_uses_real_titles_and_our_facts() -> None:
    heads = _heads()
    judged = {
        "kr": {
            "themes": [
                {"name": "전력기기", "why": "수출 증가", "evidence": ["K2"], "confidence": 0.7}
            ],
            "picks": [
                {
                    "symbol": "267260",
                    "name": "HD현대일렉트릭",
                    "theme": "전력기기",
                    "why": "대표",
                    "evidence": ["K2"],
                }
            ],
            "risks": ["과열"],
        }
    }
    facts = {"267260": {"trend": True, "ret_3m": 0.123, "in_lists": ["가격 주도주"]}}
    diff = {"new_themes": ["전력기기"], "gone_themes": [], "new_picks": [], "gone_picks": []}
    text = format_market("kr", judged, heads["kr"], facts, diff, "2026-10-10")
    assert "근거 [K2] 전력기기 수출 사상 최대 - 한경" in text
    assert "[추세 위 · 3 달 +12.3% · 가격 주도주]" in text
    assert "지난주 대비: 새 테마 전력기기" in text and "검증된 매매 신호 아님" in text


def test_diff_weeks() -> None:
    this = {"us": {"themes": [{"name": "AI"}, {"name": "원전"}], "picks": [{"symbol": "NVDA"}]}}
    last = {"us": {"themes": [{"name": "AI"}, {"name": "방산"}], "picks": [{"symbol": "LMT"}]}}
    d = diff_weeks(this, last, "us")
    assert d == {
        "new_themes": ["원전"],
        "gone_themes": ["방산"],
        "new_picks": ["NVDA"],
        "gone_picks": ["LMT"],
    }
