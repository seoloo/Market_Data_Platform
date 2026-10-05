"""연구 기록 화면 — "무엇을 연구했나" 한 줄 요약."""

from __future__ import annotations

from pathlib import Path

from auto_trading.ui.data import load_dashboard
from auto_trading.ui.render import render_page

EVIL = "<script>alert(1)</script>"


def _root(tmp_path: Path, body: str) -> Path:
    docs = tmp_path / "docs" / "current"
    docs.mkdir(parents=True)
    (docs / "RESEARCH_HISTORY.md").write_text(body, encoding="utf-8")
    return tmp_path


def test_summary_line_is_shown_with_verdict_detail_folded(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        "## T — PASS (2026-09-24)\n\n요약: BTC·ETH 추세 따라가기 — 통과\n\n"
        "Hypothesis: 긴 가설 문장.\n판정 2018~2026: 일간 평균 0.060% → PASS\n",
    )
    data = load_dashboard(root, 0)
    entry = data.research[0]
    assert entry.about == "BTC·ETH 추세 따라가기 — 통과"
    assert entry.summary.startswith("판정 2018")
    html = render_page(data, "research")
    assert "BTC·ETH 추세 따라가기 — 통과" in html
    assert "<details><summary>자세히</summary>" in html
    assert "<b>무엇을 시험했나</b> 긴 가설 문장." in html and "<b>결과</b> 판정 2018" in html


def test_without_summary_falls_back_to_short_hypothesis(tmp_path: Path) -> None:
    long = "가" * 200
    root = _root(
        tmp_path,
        f"## X{EVIL} — REJECTED (2026-09-20)\n\nHypothesis: {long}. 둘째 문장.\n"
        "## Y — REJECTED\n\nHypothesis:\n- 여러 줄 가설 첫 항목\n- 둘째\n",
    )
    data = load_dashboard(root, 0)
    x, y = data.research
    assert len(x.about) == 90 and x.about.endswith("…")
    assert y.about == "여러 줄 가설 첫 항목"
    assert EVIL not in render_page(data, "research")
