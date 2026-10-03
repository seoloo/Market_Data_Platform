"""뉴스·AI 판단 기록 (문서 115 2 단계): 질문 만들기·답 검사·겹침."""

from __future__ import annotations

import json

import pytest

from auto_trading.config.secrets import load_openai_key
from auto_trading.ops.news_ai import build_messages, compare, parse_reply

UNIVERSE = ["NVDA", "AMD", "HPE", "XOM"]


def test_messages_contain_only_headlines_not_mechanical_leaders() -> None:
    report = {
        "date": "2026-10-02",
        "leaders": [{"symbol": "HPE"}, {"symbol": "AMD"}],
        "leading_sectors": ["Information Technology"],
        "spy_above_200": True,
        "breadth_above_50ma": 0.25,
    }
    items = [
        {
            "source": "cnbc",
            "title": "Chips rally",
            "summary": "AI demand",
            "fetched_at": "2026-10-03T00:00:00+00:00",
        },
        {
            "source": "fed",
            "title": "Fed holds",
            "summary": "",
            "fetched_at": "2026-10-03T00:01:00+00:00",
        },
    ]
    msgs = build_messages(report, items, UNIVERSE)
    user = msgs[1]["content"]
    assert "ONLY the headlines" in msgs[0]["content"]
    assert "2026-10-03T00:01:00+00:00" in user  # 가장 늦게 모은 시각
    assert "- [cnbc] Chips rally — AI demand" in user and "- [fed] Fed holds" in user
    assert (
        "HPE, AMD" not in user and "Information Technology" not in user
    )  # 기계 규칙 결과는 안 준다
    assert "NVDA AMD HPE XOM" in user


def test_parse_reply_filters_universe_and_validates() -> None:
    reply = json.dumps(
        {
            "leading_sectors": ["Information Technology", "Energy", "Utilities", "Extra"],
            "leaders": [{"symbol": "nvda", "reason": "AI"}, {"symbol": "TSM", "reason": "x"}],
            "market_room": "Up",
            "confidence": 1.7,
            "risks": ["금리"],
            "summary": "요약",
        }
    )
    out = parse_reply(reply, UNIVERSE)
    assert out["leaders"] == [{"symbol": "NVDA", "reason": "AI"}]
    assert out["outside_universe"] == ["TSM"]
    assert out["market_room"] == "up" and out["confidence"] == 1.0
    assert len(out["leading_sectors"]) == 3
    with pytest.raises(ValueError):
        parse_reply('{"market_room": "maybe"}', UNIVERSE)
    with pytest.raises(ValueError):
        parse_reply("not json", UNIVERSE)


def test_compare_overlap() -> None:
    assert compare(["HPE", "AMD"], ["AMD", "NVDA"]) == {
        "both": ["AMD"],
        "only_mechanical": ["HPE"],
        "only_ai": ["NVDA"],
    }


def test_openai_key_from_either_name_and_hidden() -> None:
    assert load_openai_key("missing.env", {}) is None
    key = load_openai_key("missing.env", {"CHATGPT_KEY": "sk-test-123"})
    assert key is not None and key.value == "sk-test-123"
    assert "sk-" not in repr(key) and "sk-" not in str(key)
    first = load_openai_key("missing.env", {"OPENAI_API_KEY": "a", "CHATGPT_KEY": "b"})
    assert first is not None and first.value == "a"
