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


def test_laya_skipped_without_base_url(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LAYA_API_KEY=abc\n", encoding="utf-8")
    assert info_bot.laya_labels(_items(), tmp_path) == ({}, 0.0)


def test_laya_sends_english_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "LAYA_API_KEY=abc\nLAYA_BASE_URL=https://laya.example/\n", encoding="utf-8"
    )
    seen: list[dict[str, Any]] = []

    def post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
        assert (
            url == "https://laya.example/v1/systemone" and headers["Authorization"] == "Bearer abc"
        )
        seen.append(json)
        choice = "good" if "jumps" in json["state"] else "bad"
        return httpx.Response(200, json={"answers": {"impact": {"choice": choice}}})

    monkeypatch.setattr("scripts.info_bot.httpx.post", post)
    labels, ms = info_bot.laya_labels(_items(), tmp_path)
    assert labels == {"N2": "호재", "N3": "악재"} and ms >= 0
    assert [s["state"] for s in seen] == [
        "IREN jumps on Microsoft deal - Reuters",
        "IREN misses revenue estimates",
    ]  # 한국어 기사는 보내지 않음
    assert "impact" in seen[0]["questions"] and "model" not in seen[0]
