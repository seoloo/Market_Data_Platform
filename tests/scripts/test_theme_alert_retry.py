"""주간 테마: AI 답이 깨지면 한 번 더 묻고, 깨진 답은 파일로 남긴다 (2026-10-10 실패)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import scripts.theme_alert as mod


def _stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answers: list[str]) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(mod, "REPORTS", tmp_path)
    monkeypatch.setattr(mod, "_week_news", lambda folder, today: [])
    monkeypatch.setattr(mod, "_kr_side", lambda: ({}, {}))
    monkeypatch.setattr(mod, "_us_side", lambda: ({}, {}))
    monkeypatch.setattr(mod, "_ask", lambda messages: {"text": answers.pop(0), "model": "m"})

    def send(texts: list[str], telegram: bool) -> list[str]:
        sent.extend(texts)
        return []

    monkeypatch.setattr(mod, "_send", send)

    def parse(text: str, universes: Any, headlines: Any) -> dict[str, Any]:
        if text == "bad":
            raise ValueError("JSON 아님")
        return {"summary": "", "dropped": 0, "kr": {"picks": []}, "us": {"picks": []}}

    monkeypatch.setattr(mod, "parse_weekly", parse)
    monkeypatch.setattr(mod, "format_market", lambda *a: "ok")
    return sent


def test_retries_once_after_broken_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _stub(monkeypatch, tmp_path, ["bad", "good"])
    out = mod.weekly(telegram=False)
    assert "error" not in out and sent == ["ok", "ok"]
    assert len(list(tmp_path.glob("bad-answer-*-1.txt"))) == 1


def test_gives_up_after_two_broken_answers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sent = _stub(monkeypatch, tmp_path, ["bad", "bad"])
    out = mod.weekly(telegram=False)
    assert "답 형식 오류" in out["error"] and "2 번" in sent[0]
    assert len(list(tmp_path.glob("bad-answer-*.txt"))) == 2
