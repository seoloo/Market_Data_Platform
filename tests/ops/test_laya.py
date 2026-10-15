"""Laya 클라이언트 · 위험 뉴스 · 기사 분류 (사용자 2026-10-11) — 순수 함수와 가짜 HTTP 만."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.ops import laya_client, laya_tags, risk_news
from auto_trading.ops.risk_news import Asset, Headline

BTC = Asset("BTC", "Bitcoin", "bitcoin", group="Vega")
TQQQ = Asset("TQQQ", "ProShares UltraPro QQQ (TQQQ)", "TQQQ", "TQQQ", "Deneb")


def _h(title: str, asset: Asset = BTC, link: str = "https://x.test/a?b=1&c=2") -> Headline:
    return Headline(asset, title, link, "2026-10-11T00:00:00Z")


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
    out = c.batch([f"s{i}" for i in range(70)], risk_news.RISK_Q)
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


# ---------------------------------------------------------------- risk_news


def test_pick_alerts_skips_routine_low_and_missing() -> None:
    items = [_h("a"), _h("b"), _h("c"), _h("d")]
    choices = [("routine", 0.99), ("hack_fraud", 0.95), ("legal", 0.6), None]
    assert [(h.title, k) for h, k, _ in risk_news.pick_alerts(items, choices)] == [
        ("b", "hack_fraud")
    ]
    wide = risk_news.pick_alerts(items, choices, risk_news.CANDIDATE_MIN)
    assert [h.title for h, _, _ in wide] == ["b", "c"]


def test_parse_confirm_keeps_only_serious_in_range() -> None:
    answer = {
        "items": [
            {"id": "R1", "serious": True, "why": "거래소 해킹"},
            {"id": "R2", "serious": False, "why": "전망"},
            {"id": "R9", "serious": True, "why": "범위 밖"},
            {"id": "X", "serious": True},
            "junk",
        ]
    }
    assert risk_news.parse_confirm(answer, 2) == {1: "거래소 해킹"}
    assert risk_news.parse_confirm(None, 2) == {}


def test_alert_text_escapes_outside_text() -> None:
    h = _h("<b>Exchange</b> hacked & drained", link='https://x.test/"q"')
    text = risk_news.alert_text([(h, "hack_fraud", 0.91)], {1: "<script>"})
    assert "&lt;b&gt;Exchange" in text
    assert "&lt;script&gt;" in text
    assert "&quot;q&quot;" in text
    assert "해킹 · 사기" in text and "91%" in text


def test_seen_store_dedupes_and_expires(tmp_path: Path) -> None:
    store = risk_news.SeenStore(tmp_path / "seen.json")
    a, a_dup, b = _h("Same - Reuters"), _h("Same - CNBC"), _h("Other", TQQQ)
    assert store.new([a, a_dup, b]) == [a, b]
    store.mark([a], now=1_000.0)
    store.save()
    again = risk_news.SeenStore(tmp_path / "seen.json")
    assert again.new([a_dup, b]) == [b]
    again.mark([b], now=1_000.0 + risk_news.KEEP_S + 1)
    assert a.key not in again.seen


def test_decide_without_gemini_uses_high_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import risk_news as script

    monkeypatch.setattr(script, "load_gemini_key", lambda _p: None)
    items = [_h("a"), _h("b")]
    alerts, whys = script._decide(items, [("legal", 0.7), ("delisting", 0.95)], Path())
    assert [h.title for h, _, _ in alerts] == ["b"]
    assert whys == {}


def test_decide_with_gemini_keeps_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import risk_news as script

    class Key:
        value = "g"

    monkeypatch.setattr(script, "load_gemini_key", lambda _p: Key())
    answer = {"items": [{"id": "R2", "serious": True, "why": "증자 확정"}]}
    monkeypatch.setattr("scripts.market_brief.ask_gemini", lambda *_a: (answer, []))
    items = [_h("a"), _h("b"), _h("c")]
    choices = [("legal", 0.9), ("dilution", 0.6), ("routine", 0.9)]
    alerts, whys = script._decide(items, choices, Path())
    assert [h.title for h, _, _ in alerts] == ["b"]
    assert whys == {1: "증자 확정"}


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
