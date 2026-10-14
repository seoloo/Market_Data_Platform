"""긴급 정지 스위치 — 파일 하나로 모든 실행기의 신규 주문을 멈춘다 (Phase 8 준비, docs/research/51).

``data/HALT`` 파일이 있으면 정지 상태다. 파일 내용(첫 줄)이 사유다. 파일을 지우면 풀린다.
사람이 탐색기·메모장만으로 켜고 끌 수 있게 파일로 둔다. 정지 중에도 위험 축소(청산)는 막지 않는
것이 원칙이다(AGENTS §2).

**그룹 정지** (2026-10-11, 사용자 '전부 정리' 요청): ``data/HALT_cruise`` · ``HALT_glide`` ·
``HALT_acc`` 는 그 그룹(Cruise · Glide · 자동 적립)의 새 주문만 멈춘다. 실행기는
:func:`halted_for` 로 전체 정지와 자기 그룹 정지를 함께 본다.

파일을 만들고 지우는 것은 사람의 결정뿐이다 — 설정 화면의 비상 정지 버튼, 사용자가 누른
'전부 정리'(ops/liquidate.py), 사용자가 누른 '정지 풀기'. 실행기가 스스로 만들거나 지우지 않는다.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["GROUPS", "group_path", "halted_for", "halted_reason"]

GROUPS: dict[str, str] = {"cruise": "Cruise", "glide": "Glide", "acc": "자동 적립"}
"""그룹 키 → 보이는 이름."""


def halted_reason(path: Path) -> str | None:
    """정지면 사유(비어 있으면 '사유 없음'), 아니면 None. 읽기 실패도 정지로 본다(fail-closed)."""
    if not path.exists():
        return None
    try:
        first = path.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return "정지 파일을 읽을 수 없음"
    return first[0].strip() if first and first[0].strip() else "사유 없음"


def group_path(halt: Path, group: str) -> Path:
    """``data/HALT`` 옆의 그룹 정지 파일 (``data/HALT_cruise`` 등)."""
    if group not in GROUPS:
        raise ValueError(f"모르는 그룹: {group}")
    return halt.with_name(f"{halt.name}_{group}")


def halted_for(halt: Path, group: str) -> str | None:
    """전체 정지 또는 그 그룹 정지면 사유, 아니면 None."""
    return halted_reason(halt) or halted_reason(group_path(halt, group))
