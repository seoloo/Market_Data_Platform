"""정보 봇 성적표 — 같은 날 한 번만 기록 · 20 거래일 뒤 채점 · 맞음 계산 · Laya 그림자 비교."""

from __future__ import annotations

from pathlib import Path

from auto_trading.ops.stock_calls import (
    Call,
    append_call,
    laya_text,
    load_calls,
    record_line,
    save_calls,
    score,
    score_text,
)


def _call(day: str, view: str, sym: str = "IREN", on: bool | None = False) -> Call:
    return Call(day, sym.lower(), sym, 10.0, on, view)


def test_append_once_per_day_and_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "calls.jsonl"
    assert append_call(p, _call("2026-10-09", "약세"))
    assert not append_call(p, _call("2026-10-09", "강세"))  # 같은 날 같은 종목
    assert append_call(p, _call("2026-10-09", "강세", "NVDA"))
    got = load_calls(p)
    assert [c.view for c in got] == ["약세", "강세"]
    p.write_text(p.read_text(encoding="utf-8") + "깨진 줄\n", encoding="utf-8")
    assert len(load_calls(p)) == 2


def test_score_after_horizon_and_hits(tmp_path: Path) -> None:
    rows = [(f"2026-01-{d:02d}", 10.0 + d) for d in range(1, 31)]
    calls = [_call("2026-01-01", "강세"), _call("2026-01-05", "약세"), _call("2026-01-20", "강세")]
    assert score(calls, {"IREN": rows}) == 2  # 마지막은 20 거래일이 아직 안 지남
    assert calls[0].outcome is not None and calls[0].outcome > 0
    assert calls[2].outcome is None
    text = score_text(calls)
    assert "AI 강세: 1건 · 맞음 100%" in text and "AI 약세: 1건 · 맞음 0%" in text
    assert "채점 대기 1건" in text and "추세 꺼짐: 2건" in text
    assert "5건부터" in record_line(calls)
    many = [_call(f"2026-01-0{d}", "강세") for d in range(1, 6)]
    for c in many:
        c.outcome = 0.01
    assert "채점 5건 중 맞음 100%" in record_line(many)
    p = tmp_path / "c.jsonl"
    save_calls(p, calls)
    assert load_calls(p)[0].outcome == calls[0].outcome


def test_laya_shadow_agreement() -> None:
    assert "아직 기록 없음" in laya_text([])
    c = _call("2026-01-01", "")
    c.tones = {"N1": "호재", "N2": "악재", "N3": ""}
    c.laya, c.laya_ms = {"N1": "호재", "N2": "중립", "N3": "호재"}, 40.0
    text = laya_text([c])
    assert "기사 2건" in text and "같은 판단 50%" in text and "40ms" in text
