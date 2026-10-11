"""정보 봇 — 기사 앞부분 뽑기 · Laya 그림자(영어 기사만 · 주소 없으면 건너뜀). 네트워크 없음."""

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
    assert info_bot.laya_labels(_items(), tmp_path) == ({}, 0.0)
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\nLAYA_OFF=true\n", encoding="utf-8")
    assert info_bot.laya_labels(_items(), tmp_path) == ({}, 0.0)


def test_laya_one_batch_call_english_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\n", encoding="utf-8")
    seen: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        assert headers["Authorization"] == "Bearer abc"
        seen.append((url, json))
        res = [
            {
                "answers": {
                    "impact": {
                        "choice": "good" if "jumps" in s else "bad",
                        "probabilities": {"good": 0.87, "bad": 0.98},
                    }
                }
            }
            for s in json["states"]
        ]
        return httpx.Response(200, json={"results": res, "total_usage": {"input_tokens": 9}})

    monkeypatch.setattr("scripts.info_bot.httpx.post", post)
    labels, ms = info_bot.laya_labels(_items(), tmp_path)
    assert labels == {"N2": ("호재", 0.87), "N3": ("악재", 0.98)} and ms >= 0
    assert len(seen) == 1  # 질문 하나에 한 번만 (무료 횟수 아끼기)
    url, body = seen[0]
    assert url == "https://api.laya-ai.com/v1/systemone/batch" and body["model"] == "english"
    assert body["states"] == [
        "IREN jumps on Microsoft deal - Reuters",
        "IREN misses revenue estimates",
    ]  # 한국어 기사는 보내지 않음
