"""인계 요약 — 상황판 읽기 · 지난 것 표시."""

from __future__ import annotations

from datetime import datetime

from scripts.handoff import board_summary


def _t(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm)  # noqa: DTZ001 — 상황판 시각은 PC 시계(KST, 시간대 없음)


BOARD = """에이전트: claude-x   갱신: 2026-10-16 19:32 KST
하는 일: 239 연구
잡은 파일: docs/research/239-*
상태: 끝 — 커밋 완료
결정 대기: Altair 켤지
커밋: abc1234
"""


def test_board_fields_and_fresh() -> None:
    b = board_summary(BOARD, _t(2026, 10, 16, 20, 0))
    assert b["agent"] == "claude-x"
    assert b["updated"] == "2026-10-16T19:32"
    assert not b["stale"]
    assert str(b["상태"]).startswith("끝")
    assert b["결정 대기"] == "Altair 켤지"
    assert b["커밋"] == "abc1234"


def test_board_stale_and_missing_time() -> None:
    assert board_summary(BOARD, _t(2026, 10, 18, 0, 0))["stale"]
    b = board_summary("에이전트: y   갱신: 2026-10-16\n상태: 진행 중", _t(2026, 10, 16, 23, 0))
    assert b["updated"] == "2026-10-16T00:00" and not b["stale"]
    assert board_summary("엉망", _t(2026, 10, 16))["agent"] == "?"
