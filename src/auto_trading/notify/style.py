"""텔레그램 알림 공통 모양 — 휴대폰에서 한눈에 읽히게 (한 줄에 한 항목, 구분선, 화살표).

내용은 각 알림이 정하고, 여기는 모양 조각만 둔다. 시장 브리핑(문서 IDEAS 29)과 같은 꼴.
"""

from __future__ import annotations

RULE = "━━━━━━━━━━━━"
SUB = "   "
"""항목 아래 들여쓰기."""


def arrow(x: float | None) -> str:
    """오르면 🔺, 내리면 🔻, 그대로 ▫️, 값 없음 ·."""
    if x is None:
        return "·"
    return "🔺" if x > 0 else "🔻" if x < 0 else "▫️"


def sub(text: str) -> str:
    """윗줄의 보충 설명 (``   └ …``)."""
    return f"{SUB}└ {text}"


def section(title: str) -> list[str]:
    """빈 줄 + 구분선 + 제목."""
    return ["", RULE, title]
