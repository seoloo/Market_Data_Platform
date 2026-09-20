"""결정론적 해시.

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


def _normalize(value: Any) -> Any:
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
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, frozenset | set):
        return sorted(_normalize(v) for v in value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_normalize(v) for v in value]
    if hasattr(value, "to_digest_dict"):
        return _normalize(value.to_digest_dict())
    raise TypeError(f"해시 입력으로 직렬화할 수 없는 타입: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """정규화된 JSON 문자열. 같은 내용은 항상 같은 문자열이 된다."""
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def sha256_hex(value: Any) -> str:
    """정규화 JSON 의 SHA-256 16진 문자열."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
