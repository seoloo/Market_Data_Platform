"""긴급 정지 스위치 — 파일 하나로 실행기의 새 주문을 멈춘다.

언제 쓰나: 모든 실행기(Deneb · 자동 적립 · Vega · 개별주 …)가 주문 전에 부른다.
읽는 것: ``data/HALT`` (전체 정지) · ``data/HALT_<그룹>`` (그 그룹만).
         이 모듈은 파일을 만들거나 지우지 않는다.

* 파일이 있으면 정지, 지우면 풀림. 파일 첫 줄이 정지 사유다.
* 사람이 탐색기 · 메모장만으로도 켜고 끌 수 있게 파일로 뒀다 (Phase 8 준비, docs/research/51).
* 정지 중에도 위험을 줄이는 일(청산)은 막지 않는다 (AGENTS §2).
* **그룹 정지** (2026-10-11, 사용자 '전부 정리' 요청): ``HALT_vega`` · ``HALT_deneb`` ·
  ``HALT_acc`` … 는 그 그룹의 새 주문만 멈춘다. 실행기는 :func:`halted_for` 로
  전체 정지와 자기 그룹 정지를 함께 본다.
* 파일을 만들고 지우는 것은 사람의 결정뿐이다 — 설정 화면의 비상 정지 버튼, 사용자가 누른
  '전부 정리'(ops/liquidate.py), 사용자가 누른 '정지 풀기'.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "GROUPS",
    "LEGACY",
    "group_halted",
    "group_path",
    "halted_for",
    "halted_reason",
    "legacy_path",
]

GROUPS: dict[str, str] = {
    "vega": "Vega",
    "deneb": "Deneb",
    "acc": "자동 적립",
    "stock": "개별주",
    "sirius": "Sirius",
}
"""그룹 키(정지 파일 이름 ``HALT_<키>`` 에 들어감) → 화면에 보이는 이름.
은퇴한 Altair(2026-10-19)는 리팩토링 5 단계(2026-10-21)에서 뺐다 — 남아 있던 ``HALT_altair`` 는
``data/archive/retired-2026-10-21/`` 로 옮겼다(읽는 실행기 없음)."""

LEGACY: dict[str, str] = {"vega": "cruise", "deneb": "glide"}
"""지금 그룹 키 → 2026-10-15 이름 변경 전 그룹 키.

[이유] 이름을 바꾸기 전에 만든 정지 파일(``HALT_glide`` 등)이 남아 있으면 그것도 정지로 봐야,
       이름 변경 때문에 멈춰 둔 전략이 몰래 다시 주문하지 않는다."""


def halted_reason(halt_file: Path) -> str | None:
    """[쓰임] 정지 파일 하나를 보고, 정지면 사유를 · 아니면 None 을 돌려준다.
    [이유] 파일은 있는데 읽지 못하면 '정지' 로 본다(fail-closed) — 애매하면 주문하지 않는 쪽이 안전.

    사유 줄이 비어 있으면 '사유 없음'."""
    if not halt_file.exists():
        return None
    try:
        lines = halt_file.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return "정지 파일을 읽을 수 없음"
    if lines and lines[0].strip():
        return lines[0].strip()
    return "사유 없음"


def group_path(halt: Path, group: str) -> Path:
    """[쓰임] 전체 정지 파일(``data/HALT``) 옆의 그룹 정지 파일 경로 (``data/HALT_vega`` 등).
    [이유] 모르는 그룹 이름은 오타일 가능성이 커서 바로 오류를 낸다
           (엉뚱한 파일을 보면 정지가 안 걸림)."""
    if group not in GROUPS:
        raise ValueError(f"모르는 그룹: {group}")
    return halt.with_name(f"{halt.name}_{group}")


def legacy_path(halt: Path, group: str) -> Path | None:
    """[쓰임] 이름 변경 전 그룹 정지 파일 경로 (``data/HALT_glide`` 등).

    옛 이름이 없는 그룹은 None."""
    old_group = LEGACY.get(group)
    if old_group is None:
        return None
    return halt.with_name(f"{halt.name}_{old_group}")


def group_halted(halt: Path, group: str) -> str | None:
    """[쓰임] 그 그룹만의 정지 사유 (전체 정지는 보지 않는다) — 옛 이름 정지 파일도 본다.
    [이유] 안전 점검 · 정리 화면은 '어느 그룹이 따로 멈췄나' 를 보여 줘야 해서
           전체 정지와 나눠 본다."""
    reason = halted_reason(group_path(halt, group))
    if reason is not None:
        return reason
    old_file = legacy_path(halt, group)
    if old_file is None:
        return None
    return halted_reason(old_file)


def halted_for(halt: Path, group: str) -> str | None:
    """[쓰임] 실행기가 주문 전에 부른다 — 전체 정지 또는 그 그룹 정지면 사유, 아니면 None.
    [이유] 실행기마다 두 파일을 따로 보다가 하나를 빼먹지 않게 한 함수로 묶었다."""
    return halted_reason(halt) or group_halted(halt, group)
