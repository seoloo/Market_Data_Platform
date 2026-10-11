"""정보 봇 — 기사 앞부분 뽑기 · Laya(영어 모델 · 한국어는 다국어 모델 · 키 없으면 건너뜀)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.ops.news_rss import NewsItem
from scripts import info_bot


def test_excerpt_takes_description_and_long_paragraphs() -> None:
    page = (
        '<html><head><meta property="og:description" content="IREN signs a &amp; deal"></head>'
        "<body><p>short</p><p>" + "Revenue grew strongly this quarter. " * 3 + "</p></body></html>"
    )
    text = info_bot.excerpt(page)
    assert text.startswith("IREN signs a & deal") and "Revenue grew" in text and "short" not in text


def _items() -> list[NewsItem]:
    return [
        NewsItem("gnews_ko", "아이렌 급등 - 한경", "u", "", "", ""),
        NewsItem("gnews_en", "IREN jumps on Microsoft deal - Reuters", "u", "", "", ""),
        NewsItem("yahoo_en", "IREN misses revenue estimates", "u", "", "", ""),
    ]


def test_laya_skipped_without_key_or_when_off(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OTHER=1\n", encoding="utf-8")
    assert info_bot.laya_labels(_items(), tmp_path) == ({}, 0.0, {})
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\nLAYA_OFF=true\n", encoding="utf-8")
    assert info_bot.laya_labels(_items(), tmp_path) == ({}, 0.0, {})


def test_laya_one_call_per_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\n", encoding="utf-8")
    seen: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        assert headers["Authorization"] == "Bearer abc"
        seen.append((url, json))
        res = [
            {
                "answers": {
                    "impact": {
                        "choice": "good" if ("jumps" in s or "급등" in s) else "bad",
                        "probabilities": {"good": 0.87, "bad": 0.98, "neutral": 0.05},
                    }
                }
            }
            for s in json["states"]
        ]
        return httpx.Response(200, json={"results": res, "total_usage": {"input_tokens": 9}})

    monkeypatch.setattr("scripts.info_bot.httpx.post", post)
    labels, ms, probs = info_bot.laya_labels(_items(), tmp_path)
    assert labels == {"N1": ("호재", 0.87), "N2": ("호재", 0.87), "N3": ("악재", 0.98)} and ms >= 0
    assert probs["N3"] == {"호재": 0.87, "악재": 0.98, "중립": 0.05}
    assert len(seen) == 2  # 언어마다 한 번 (무료 횟수 아끼기)
    by_model = {body["model"]: body["states"] for _, body in seen}
    assert seen[0][0] == "https://api.laya-ai.com/v1/systemone/batch"
    assert by_model["english"] == [
        "IREN jumps on Microsoft deal - Reuters",
        "IREN misses revenue estimates",
    ]
    assert by_model["multilingual"] == ["아이렌 급등 - 한경"]  # 한국어 기사는 다국어 모델로


def test_laya_english_only_items_make_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\n", encoding="utf-8")
    seen: list[str] = []

    def post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        seen.append(json["model"])
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr("scripts.info_bot.httpx.post", post)
    assert info_bot.laya_labels(_items()[1:], tmp_path) == ({}, 0.0, {})
    assert seen == ["english"]
