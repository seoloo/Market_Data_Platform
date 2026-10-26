"""파이프라인 공용 오류.

언제 쓰나: Bronze 받기 등 파이프라인 단계가 '계속하면 안 되는' 상태를 알릴 때.
읽고 쓰는 것: 없음.
"""

from __future__ import annotations

__all__ = [
    "BronzeConflictError",
    "DataIntegrityError",
    "ForbiddenRequestError",
    "SourceUnavailableError",
]


class DataIntegrityError(Exception):
    """[쓰임] 받은 자료가 원천이 밝힌 것과 다르다 — 체크섬 불일치, CHECKSUM 형식 오류 등.
    [이유] 검증 못 한 자료가 조용히 들어가면 그 뒤 모든 계산을 믿을 수 없다. 멈추고 알린다."""


class BronzeConflictError(DataIntegrityError):
    """[쓰임] Bronze 에 이미 있는 파일이 원천 체크섬과 다르다.
    [이유] Bronze 는 '받은 그대로, 바꾸지 않는다' 가 약속이라 덮어쓰지 않는다. 사람이 원인을 본 뒤
           직접 옮기거나 지우게 한다."""


class SourceUnavailableError(Exception):
    """[쓰임] 원천 서버에서 여러 번 다시 시도해도 받지 못했다(네트워크 · 5xx)."""


class ForbiddenRequestError(Exception):
    """[쓰임] 접속 창구의 허용 목록에 없는 요청 — 보내기 전에 멈춘다(예: 주문 · 잔고 · 계좌 경로).
    [이유] 증권사 키는 시세 조회에만 쓴다(절대 규칙 2). 코드 실수로도 주문 경로에 닿지 않게 한다."""
