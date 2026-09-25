"""EW-4n 순위 근거 (문서 61 §2) — 4파·B파가 플랫·수렴 삼각형으로 읽혀도 검증으로 친다.

v1(:mod:`ranking`)·v2(:mod:`ranking_v2`)는 그대로 둔다. 가이드라인 거리는 v2 와 같다.
순위는 확률이 아니다: (1) 검증된 다리 비율, (2) 가이드라인 목표와의 로그 비율 거리.
"""

from __future__ import annotations

from collections.abc import Sequence

from auto_trading.research.elliott.parser import NESTED_ALLOWED, FinePivot, Parse
from auto_trading.research.elliott.ranking import RankKey
from auto_trading.research.elliott.ranking_v2 import guideline_distance_v2

__all__ = ["rank_key_v3", "verified_nested"]


def verified_nested(parse: Parse) -> float:
    allowed = NESTED_ALLOWED[parse.pattern]
    return sum(lab in ok for lab, ok in zip(parse.leg_labels, allowed, strict=True)) / len(allowed)


def rank_key_v3(parse: Parse, pivots: Sequence[FinePivot]) -> RankKey:
    return RankKey(-verified_nested(parse), guideline_distance_v2(parse, pivots))
