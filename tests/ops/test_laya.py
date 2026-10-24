"""Laya 클라이언트 · 기사 분류 (사용자 2026-10-11) — 순수 함수와 가짜 HTTP 만.

위험 뉴스(실거래 보유 종목 알림, ``ops/risk_news``) 시험은 실주문 코드를 걷어 낼 때
함께 지웠다(README 6-3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from auto_trading.ops import laya_client, laya_tags

QUESTION: dict[str, Any] = {"risk": {"type": "choice", "choices": ["routine", "hack_fraud"]}}
"""시험용 질문 하나 (모양만 — 실제 질문 문구는 쓰는 쪽이 정한다)."""


def _ans(choice: str, prob: float) -> dict[str, Any]:
    return {"answers": {"risk": {"choice": choice, "probabilities": {choice: prob}}}}


# ---------------------------------------------------------------- laya_client


def test_choice_of_reads_probability_and_tolerates_junk() -> None:
    assert laya_client.choice_of(_ans("hack_fraud", 0.93), "risk") == laya_client.Choice(
        "hack_fraud", 0.93
    )
    assert laya_client.choice_of(None, "risk") is None
    assert laya_client.choice_of({"answers": {}}, "risk") is None
    no_probs = {"answers": {"risk": {"choice": "routine", "answer_confidence": 0.7}}}
    assert laya_client.choice_of(no_probs, "risk") == laya_client.Choice("routine", 0.7)


def test_batch_chunks_and_fills_failures_with_none() -> None:
    calls: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        assert req.headers["Authorization"] == "Bearer k"
        if len(calls) == 2:
            return httpx.Response(500)
        n = len(body["states"])
        return httpx.Response(
            200,
            json={"results": [_ans("routine", 0.9)] * n, "total_usage": {"input_tokens": 10}},
        )

    c = laya_client.LayaClient("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = c.batch([f"s{i}" for i in range(70)], QUESTION)
    assert [len(b["states"]) for b in calls] == [64, 6]
    assert all(r is not None for r in out[:64])
    assert out[64:] == [None] * 6
    assert c.tokens == 10
    assert "k" not in repr(c)


def test_from_env_respects_off_switch(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\nLAYA_OFF=true\n", encoding="utf-8")
    assert laya_client.from_env(tmp_path) is None
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\n", encoding="utf-8")
    c = laya_client.from_env(tmp_path)
    assert c is not None
    c.close()


# ---------------------------------------------------------------- laya_tags


def test_brief_tally_drops_only_confident_irrelevant() -> None:
    labels = [
        (("market", 0.9), ("good", 0.8)),
        (("other", 0.9), ("bad", 0.9)),
        (("other", 0.6), ("bad", 0.7)),
        (None, None),
    ]
    keep, t = laya_tags.brief_tally(labels)
    assert keep == [True, False, True, True]
    assert (t["good"], t["bad"], t["neutral"], t["judged"], t["dropped"]) == (1, 1, 0, 2, 1)
    line = laya_tags.brief_line(t)
    assert "호재 1" in line and "악재 1" in line and "1건 뺌" in line
    assert laya_tags.brief_line({}) == ""


def test_theme_counts_and_lines_compare_last_week() -> None:
    labels = [
        ("ai_semis", 0.9),
        ("ai_semis", 0.7),
        ("market", 0.9),
        ("other", 0.9),
        ("crypto", 0.4),
        None,
        ("bogus", 0.9),
        ("energy", 0.8),
    ]
    now = laya_tags.theme_counts(labels)
    assert now == {"ai_semis": 2, "energy": 1}
    lines = laya_tags.theme_lines(now, {"ai_semis": 5, "energy": 1}, 8)
    assert lines[1] == "• AI · 반도체 2건 (지난주 대비 ▼3)"
    assert lines[2] == "• 에너지 · 원자력 · 전력 1건 (지난주 대비 ＝)"
    assert laya_tags.theme_lines({}, None, 0) == []
    assert "지난주" not in laya_tags.theme_lines(now, None, 8)[1]
