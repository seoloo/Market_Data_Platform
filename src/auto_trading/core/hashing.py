"""결정론적 해시 — 같은 내용이면 언제 · 어디서 돌려도 같은 지문(SHA-256)을 만든다.

언제 쓰나: 설정 · 코드 버전 등록(storage/repositories/meta), 연구 입력 고정(해시를 코드에 박아 두는
           곳), 백테스트 결과 재현 확인 등 "이 입력이 그때와 같은가" 를 물을 때.
읽고 쓰는 것: 없음 (순수 함수 — 파일 · 시계 · 네트워크를 쓰지 않는다).

재현성의 기반이다 (03-data-model.md §2). 같은 입력은 어떤 실행에서도 같은 해시를 낳아야 한다.

- ``sort_keys=True`` 로 dict 순서 의존성 제거
- ``Decimal`` 은 문자열로 직렬화 (float 변환 금지)
- ``set`` 은 정렬된 리스트로 직렬화
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import Enum
from typing import Any

__all__ = ["canonical_json", "sha256_hex"]


def _to_hashable(value: Any) -> Any:
    """[쓰임] 해시에 넣을 값을 JSON 으로 쓸 수 있는 '정해진 모양' 으로 바꾼다 (안쪽 값까지).
    [이유] 같은 뜻인데 모양이 다른 값(``1.10`` 과 ``1.1``, 순서만 다른 set)이 다른 해시가 되면
           "그때와 같은 입력인가" 를 판단할 수 없다. 모르는 타입은 넘기지 않고 오류를 낸다."""
    if isinstance(value, Decimal):
        # normalize() 로 1.10 과 1.1 을 같은 해시로 만든다.
        return f"D:{value.normalize():f}"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool | int | str) or value is None:
        return value
    if isinstance(value, float):
        # float 은 플랫폼별 표현 차이가 있어 해시 입력으로 위험하다.
        # 점수처럼 불가피한 경우를 위해 고정 자릿수로 양자화한다.
        return f"F:{value:.12g}"
    if isinstance(value, Mapping):
        return {str(key): _to_hashable(item) for key, item in value.items()}
    if isinstance(value, frozenset | set):
        return sorted(_to_hashable(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_to_hashable(item) for item in value]
    if hasattr(value, "to_digest_dict"):
        return _to_hashable(value.to_digest_dict())
    raise TypeError(f"해시 입력으로 직렬화할 수 없는 타입: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """[쓰임] 값을 정해진 규칙의 JSON 문자열로 만든다 — 같은 내용은 항상 같은 문자열.
    [이유] 키 순서 · 공백 · 한국어 표기가 실행마다 달라지면 해시가 달라진다. 그래서 키 정렬, 공백
           없는 구분자, 한국어 그대로(``ensure_ascii=False``)로 고정한다."""
    return json.dumps(
        _to_hashable(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def sha256_hex(value: Any) -> str:
    """[쓰임] :func:`canonical_json` 문자열의 SHA-256 을 16진 문자열(64 자)로 돌려준다.
    [이유] 설정 · 입력 전체를 저장하지 않고도 '같은가' 를 64 자 지문 하나로 비교할 수 있다."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
