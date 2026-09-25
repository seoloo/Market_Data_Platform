"""긴급 정지 스위치 — 파일 하나로 모든 실행기의 신규 주문을 멈춘다 (Phase 8 준비, docs/research/51).

``data/HALT`` 파일이 있으면 정지 상태다. 파일 내용(첫 줄)이 사유다. 파일을 지우면 풀린다.
사람이 탐색기·메모장만으로 켜고 끌 수 있게 파일로 둔다. 코드는 파일을 **만들거나 지우지 않는다** —
켜고 끄는 것은 사람의 결정이다. 정지 중에도 위험 축소(청산)는 막지 않는 것이 원칙이다(AGENTS §2).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["halted_reason"]


def halted_reason(path: Path) -> str | None:
    """정지면 사유(비어 있으면 '사유 없음'), 아니면 None. 읽기 실패도 정지로 본다(fail-closed)."""
    if not path.exists():
        return None
    try:
        first = path.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return "정지 파일을 읽을 수 없음"
    return first[0].strip() if first and first[0].strip() else "사유 없음"
