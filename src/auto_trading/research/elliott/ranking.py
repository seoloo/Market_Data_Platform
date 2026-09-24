"""EW-3d 후보 순위 근거 (33 §28, 순수 함수) — 나중에 JEV 같은 평가자로 바꿀 자리.

순위는 확률이 아니다. 사전식 두 근거만 쓴다: (1) 검증된 다리 비율(높을수록 위),
(2) 교과서 가이드라인 목표값과의 로그 비율 거리(작을수록 위). 가중치를 맞추지 않는다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from auto_trading.research.elliott.parser import FinePivot, Label, Parse

__all__ = ["GUIDELINE_TARGETS", "RankKey", "guideline_distance", "pessimistic_rank", "rank_key"]

GUIDELINE_TARGETS: dict[Label, tuple[tuple[int, int, float], ...]] = {
    # (분자 다리, 분모 다리, 목표) — 다리 번호는 0 부터
    Label.IMPULSE: ((1, 0, 0.618), (3, 2, 0.382), (2, 0, 1.618), (4, 0, 1.0)),
    Label.ZIGZAG: ((1, 0, 0.618), (2, 0, 1.0)),
}
"""엘갤 PDF p7(impulse)·p12(zigzag) 대표값. 합성 생성기의 추출 구간이 아니다."""


@dataclass(frozen=True, slots=True, order=True)
class RankKey:
    """작을수록 위. (−검증 비율, 가이드라인 거리)."""

    neg_verified: float
    distance: float


def _verified(parse: Parse) -> float:
    need = (Label.IMPULSE, Label.ZIGZAG) * 3
    return sum(lab is want for lab, want in zip(parse.leg_labels, need, strict=False)) / len(
        parse.leg_labels
    )


def guideline_distance(parse: Parse, pivots: Sequence[FinePivot]) -> float:
    legs = [
        abs(math.log(float(pivots[b].price)) - math.log(float(pivots[a].price)))
        for a, b in zip(parse.bounds, parse.bounds[1:], strict=False)
    ]
    terms = [
        abs(math.log(legs[num] / legs[den] / target))
        for num, den, target in GUIDELINE_TARGETS[parse.pattern]
        if legs[den] > 0 and legs[num] > 0
    ]
    return sum(terms) / len(terms) if terms else math.inf


def rank_key(parse: Parse, pivots: Sequence[FinePivot]) -> RankKey:
    return RankKey(-_verified(parse), guideline_distance(parse, pivots))


def pessimistic_rank(
    truth: Parse, candidates: Sequence[Parse], pivots: Sequence[FinePivot]
) -> int | None:
    """진짜 해석의 순위 = 1 + (근거가 같거나 더 좋은 다른 후보 수). 목록에 없으면 None."""
    if truth not in candidates:
        return None
    key = rank_key(truth, pivots)
    return 1 + sum(1 for c in candidates if c != truth and rank_key(c, pivots) <= key)
