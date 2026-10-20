"""장부 파일 읽기 · 쓰기 — 실행기들이 JSON 장부를 같은 방법으로 다루게 하는 작은 창구.

언제 쓰나: 실행기 · 설정 · 안전 점검이 장부(``data/**/*.json``)를 읽고 쓸 때.
읽고 쓰는 것: 부르는 쪽이 넘긴 파일 하나 (쓸 때는 옆에 ``<이름>.tmp`` 를 잠깐 만든다).

디자인 패턴: **Repository** (장부 저장 방법을 한 곳에 모음) — 리팩토링 2 단계(2026-10-20).
파일 형식(키 · 들여쓰기 · 끝 줄바꿈)은 원래 실행기마다 쓰던 그대로 둔다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["read_json_or_none", "write_json_atomic"]


def read_json_or_none(path: Path) -> Any:
    """[쓰임] 장부 파일을 읽어 JSON 값으로 돌려준다. 파일이 없으면 None.
    [이유] '처음 실행이라 장부가 없음' 과 '장부가 깨짐' 을 구분하려고, 깨진 JSON 은 숨기지 않고
           오류(ValueError)를 그대로 낸다 — 부르는 쪽이 어떻게 할지 정한다."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(
    path: Path,
    body: Any,
    *,
    newline_at_end: bool = False,
    default: Callable[[Any], Any] | None = None,
) -> None:
    """[쓰임] 장부를 ``<이름>.tmp`` 에 다 쓴 뒤 한 번에 바꿔 끼운다 (한국어 그대로 · 들여쓰기 1).
    [이유] 쓰는 도중 PC 가 꺼지거나 오류가 나도 장부가 반쯤 쓰인 채로 남지 않게 한다
           (반쯤 쓰인 장부는 다음 실행에서 읽히지 않아 주문이 멈추거나 틀린다).

    ``newline_at_end`` — 토스 장부처럼 끝에 줄바꿈 한 줄을 붙이던 파일.
    ``default`` — JSON 이 모르는 값(Decimal 등)을 바꾸는 함수 (Vega 장부는 ``str``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, ensure_ascii=False, indent=1, default=default)
    if newline_at_end:
        text += "\n"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
