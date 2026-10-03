"""EW-5 (문서 100): 대각선·이중 지그재그·이중 3·확장/러닝 삼각형 최상위 후보 — 순수 함수.

기존 :class:`WaveParser` (EW-3d′ · EW-4 · EW-4n)는 바꾸지 않는다. 이 하위 클래스가 새 모양만 더한다.
규칙은 문서 100 §3. 각 다리는 시작 극단(relaxed)이어야 하고, 다리 모양은 파싱 여부만 라벨로 남긴다
(미검증이면 OPAQUE — 순위의 검증 비율이 가른다).
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from itertools import pairwise

from auto_trading.core.types import Candle
from auto_trading.research.elliott.h4_evidence import FourHourPivotEvidence
from auto_trading.research.elliott.parser import FinePivot, Label, Parse, WaveParser

__all__ = ["EW5_LABELS", "WaveParserV5", "ZigzagKind", "zigzag_kind"]

EW5_LABELS = frozenset(
    {
        Label.ENDING_DIAGONAL,
        Label.LEADING_DIAGONAL,
        Label.DOUBLE_ZIGZAG,
        Label.DOUBLE_THREE,
        Label.EXPANDING_TRIANGLE,
        Label.RUNNING_TRIANGLE,
    }
)
_FLAT_ONLY = frozenset({Label.FLAT})
_FLAT_OR_TRIANGLE = frozenset({Label.FLAT, Label.TRIANGLE})


class ZigzagKind(StrEnum):
    TRUNCATED = "TRUNCATED"
    """C < 0.618 A — 뒤따르는 움직임이 지그재그 전체를 80% 이상 되돌린다는 NEoWave 지침."""
    NORMAL = "NORMAL"
    ELONGATED = "ELONGATED"
    """C > 1.618 A — 충격파를 가장 잘 흉내 낸다."""


def zigzag_kind(parse: Parse, pivots: Sequence[FinePivot]) -> ZigzagKind:
    """지그재그 세 종류 (문서 100 §3, NEoWave): C 길이 ÷ A 길이."""
    if parse.pattern is not Label.ZIGZAG:
        raise ValueError(f"지그재그가 아니다: {parse.pattern}")
    p0, pa, pb, pc = (float(pivots[k].price) for k in parse.bounds)
    a, c = abs(pa - p0), abs(pc - pb)
    ratio = c / a if a > 0 else 0.0
    if ratio < 0.618:
        return ZigzagKind.TRUNCATED
    if ratio > 1.618:
        return ZigzagKind.ELONGATED
    return ZigzagKind.NORMAL


class WaveParserV5(WaveParser):
    """EW-4n 파서(``nested=True``) + 문서 100 의 새 최상위 모양."""

    def __init__(
        self,
        pivots: Sequence[FourHourPivotEvidence],
        bars: Sequence[Candle],
        *,
        lenient_three: bool = True,
    ) -> None:
        super().__init__(pivots, bars, lenient_three=lenient_three, nested=True)
        self._rel_sets = [frozenset(r) for r in self._relaxed]

    def _zz(self, i: int, j: int) -> Label | None:
        return self._leg(i, j, Label.ZIGZAG, relaxed=True)

    def pattern_candidates_v5(self, i: int, j: int) -> tuple[Parse, ...]:
        """i 에서 시작해 j 에서 끝나는 EW-5 새 모양 후보 전부 (규칙만, 순위 없음)."""
        if j - i < 3 or (j - i) % 2 == 0:
            return ()
        d = self._direction(i)
        q = [d * f for f in self._qf]
        rel, rset = self._relaxed, self._rel_sets
        found: list[Parse] = []

        def length(a: int, b: int) -> float:
            return abs(q[b] - q[a])

        # ---------------------------------------------------- 세 다리: 이중 지그재그 · 이중 3
        for b1 in rel[i]:
            if b1 >= j:
                break
            if q[b1] <= q[i]:
                continue
            for b2 in rel[b1]:
                if b2 >= j:
                    break
                if j not in rset[b2]:
                    continue
                lw, lx, ly = length(i, b1), length(b1, b2), length(b2, j)
                if not 0 < lx < lw or q[b2] <= q[i]:
                    continue
                if q[j] > q[b1] and ly >= lx:
                    legs = (self._zz(i, b1), self._zz(b1, b2), self._zz(b2, j))
                    if None not in legs:
                        found.append(Parse(Label.DOUBLE_ZIGZAG, (i, b1, b2, j), legs))  # type: ignore[arg-type]
                if lx >= 0.5 * lw and 0.618 * lw <= ly < 2 * lw:
                    w = self._leg(i, b1, Label.ZIGZAG, relaxed=True, alts=_FLAT_ONLY)
                    x = self._zz(b1, b2)
                    y = self._leg(b2, j, Label.ZIGZAG, relaxed=True, alts=_FLAT_OR_TRIANGLE)
                    if (
                        w is not None
                        and x is not None
                        and y is not None
                        and not (w is Label.ZIGZAG and y is Label.ZIGZAG)
                    ):
                        found.append(Parse(Label.DOUBLE_THREE, (i, b1, b2, j), (w, x, y)))
        if j - i < 5:
            return tuple(found)

        # ---------------------------------------------------- 다섯 다리: 대각선 · 확장/러닝 삼각형
        for b1 in rel[i]:
            if b1 >= j - 3:
                break
            if q[b1] <= q[i]:
                continue
            for b2 in rel[b1]:
                if b2 >= j - 2:
                    break
                for b3 in rel[b2]:
                    if b3 >= j - 1:
                        break
                    for b4 in rel[b3]:
                        if b4 >= j:
                            break
                        if j not in rset[b4]:
                            continue
                        found.extend(self._five(i, b1, b2, b3, b4, j, q))
        return tuple(found)

    def _five(
        self, i: int, b1: int, b2: int, b3: int, b4: int, j: int, q: Sequence[float]
    ) -> list[Parse]:
        bounds = (i, b1, b2, b3, b4, j)
        lens = [abs(q[b] - q[a]) for a, b in pairwise(bounds)]
        l1, l2, l3, l4, l5 = lens
        out: list[Parse] = []
        diagonal = (
            q[b2] > q[i]
            and q[b3] > q[b1]
            and q[b2] < q[b4] < q[b1]
            and q[j] > q[b3]
            and l1 > l3 > l5
            and l2 > l4
            and l5 > 0.5 * l4
        )
        if diagonal:
            zz = tuple(self._zz(a, b) for a, b in pairwise(bounds))
            if None not in zz:
                out.append(Parse(Label.ENDING_DIAGONAL, bounds, zz))  # type: ignore[arg-type]
            motive = (
                self._leg(i, b1, Label.IMPULSE, relaxed=True),
                zz[1],
                self._leg(b2, b3, Label.IMPULSE, relaxed=True),
                zz[3],
                self._leg(b4, j, Label.IMPULSE, relaxed=True),
            )
            if None not in motive:
                out.append(Parse(Label.LEADING_DIAGONAL, bounds, motive))  # type: ignore[arg-type]
        expanding = (
            q[b2] < q[i]
            and q[b3] > q[b1]
            and q[b4] < q[b2]
            and q[j] > q[b3]
            and all(1.0 < lens[k + 1] / lens[k] <= 1.5 for k in range(4))
        )
        running = (
            q[b2] < q[i]
            and q[b3] < q[b1]
            and q[b4] > q[b2]
            and q[j] < q[b3]
            and l3 < l2
            and l4 < l3
            and l5 < l4
        )
        if expanding or running:
            zz = tuple(self._zz(a, b) for a, b in pairwise(bounds))
            if None not in zz:
                label = Label.EXPANDING_TRIANGLE if expanding else Label.RUNNING_TRIANGLE
                out.append(Parse(label, bounds, zz))  # type: ignore[arg-type]
        return out
