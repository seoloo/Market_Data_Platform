"""긴급 정지 스위치 (51): 파일이 있으면 정지, 첫 줄이 사유."""

from __future__ import annotations

from pathlib import Path

from auto_trading.ops.kill_switch import halted_reason


def test_no_file_means_running(tmp_path: Path) -> None:
    assert halted_reason(tmp_path / "HALT") is None


def test_first_line_is_reason(tmp_path: Path) -> None:
    path = tmp_path / "HALT"
    path.write_text("거래소 점검\n두 번째 줄", encoding="utf-8")
    assert halted_reason(path) == "거래소 점검"


def test_empty_file_still_halts(tmp_path: Path) -> None:
    path = tmp_path / "HALT"
    path.write_text("", encoding="utf-8")
    assert halted_reason(path) == "사유 없음"


def test_unreadable_file_halts(tmp_path: Path) -> None:
    path = tmp_path / "HALT"
    path.write_bytes(b"\xff\xfe\xfa")
    assert halted_reason(path) == "정지 파일을 읽을 수 없음"
