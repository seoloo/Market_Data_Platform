"""EW-5 순위 근거 (문서 100 §4) — v3 에 새 모양의 대표 비율을 더한다.

v1·v2·v3 는 그대로 둔다(기존 결과 재현). 순위는 확률이 아니다:
(1) 다리 자리마다 허용 모양으로 검증된 비율, (2) 교과서 대표 비율과의 로그 거리.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from auto_trading.research.elliott.parser import NESTED_ALLOWED, FinePivot, Label, Parse
from auto_trading.research.elliott.ranking import RankKey
from auto_trading.research.elliott.ranking_v2 import GUIDELINE_TARGETS_V2

__all__ = ["GUIDELINE_TARGETS_V5", "guideline_distance_v5", "rank_key_v5"]

GUIDELINE_TARGETS_V5: dict[Label, tuple[tuple[int, int, float], ...]] = {
    **GUIDELINE_TARGETS_V2,
    # (분자 다리, 분모 다리, 목표) — 대표값 하나씩, 맞추지 않는다 (문서 100 §4)
    Label.ENDING_DIAGONAL: ((2, 0, 0.618), (4, 2, 0.618), (3, 1, 0.618)),
    Label.LEADING_DIAGONAL: ((2, 0, 0.618), (4, 2, 0.618), (3, 1, 0.618)),
    Label.DOUBLE_ZIGZAG: ((1, 0, 0.618), (2, 0, 1.0)),
    Label.DOUBLE_THREE: ((1, 0, 0.9), (2, 0, 1.0)),
    Label.EXPANDING_TRIANGLE: ((1, 0, 1.236), (2, 1, 1.236), (3, 2, 1.236), (4, 3, 1.236)),
    Label.RUNNING_TRIANGLE: ((1, 0, 1.236), (2, 1, 0.618), (3, 2, 0.618), (4, 3, 0.618)),
}


def _verified(parse: Parse) -> float:
    allowed = NESTED_ALLOWED[parse.pattern]
    return sum(lab in ok for lab, ok in zip(parse.leg_labels, allowed, strict=True)) / len(allowed)


def guideline_distance_v5(parse: Parse, pivots: Sequence[FinePivot]) -> float:
    legs = [
        abs(math.log(float(pivots[b].price)) - math.log(float(pivots[a].price)))
        for a, b in zip(parse.bounds, parse.bounds[1:], strict=False)
    ]
    terms = [
        abs(math.log(legs[num] / legs[den] / target))
        for num, den, target in GUIDELINE_TARGETS_V5[parse.pattern]
        if legs[den] > 0 and legs[num] > 0
    ]
    return sum(terms) / len(terms) if terms else math.inf


def rank_key_v5(parse: Parse, pivots: Sequence[FinePivot]) -> RankKey:
    return RankKey(-_verified(parse), guideline_distance_v5(parse, pivots))
