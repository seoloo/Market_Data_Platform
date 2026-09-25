"""EW-4 순위 근거 (문서 60 §3.3) — 동결된 v1(:mod:`ranking`)에 플랫·삼각형을 더한다.

충격파·지그재그는 v1 과 **같은 값**을 낸다(테스트로 고정).
v1 파일은 건드리지 않는다(JEV 기준선 해시 유지).
순위는 확률이 아니다: (1) 검증된 다리 비율, (2) 가이드라인 목표와의 로그 비율 거리.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from auto_trading.research.elliott.parser import LEG_NEEDS, FinePivot, Label, Parse
from auto_trading.research.elliott.ranking import GUIDELINE_TARGETS, RankKey

__all__ = ["GUIDELINE_TARGETS_V2", "guideline_distance_v2", "rank_key_v2"]

GUIDELINE_TARGETS_V2: dict[Label, tuple[tuple[int, int, float], ...]] = {
    **GUIDELINE_TARGETS,
    Label.FLAT: ((1, 0, 1.0), (2, 0, 1.0)),
    Label.TRIANGLE: ((1, 0, 0.618), (2, 1, 0.618), (3, 2, 0.618), (4, 3, 0.618)),
}
"""플랫: 정규형 B/A = C/A = 1. 삼각형: 연속 다리 0.618 (교과서 대표값, 합성 추출 구간이 아니다)."""


def _verified(parse: Parse) -> float:
    need = LEG_NEEDS[parse.pattern]
    return sum(lab is want for lab, want in zip(parse.leg_labels, need, strict=True)) / len(need)


def guideline_distance_v2(parse: Parse, pivots: Sequence[FinePivot]) -> float:
    legs = [
        abs(math.log(float(pivots[b].price)) - math.log(float(pivots[a].price)))
        for a, b in zip(parse.bounds, parse.bounds[1:], strict=False)
    ]
    terms = [
        abs(math.log(legs[num] / legs[den] / target))
        for num, den, target in GUIDELINE_TARGETS_V2[parse.pattern]
        if legs[den] > 0 and legs[num] > 0
    ]
    return sum(terms) / len(terms) if terms else math.inf


def rank_key_v2(parse: Parse, pivots: Sequence[FinePivot]) -> RankKey:
    return RankKey(-_verified(parse), guideline_distance_v2(parse, pivots))
