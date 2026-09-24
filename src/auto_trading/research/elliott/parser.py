"""EW-3: 4h 꼭짓점 열을 Elliott 문법으로 파싱해 가능한 모든 해석을 낸다 (33 §24, 순수 함수).

degree 를 고정 % 임계로 정하지 않는다. 가장 촘촘한 꼭짓점 열에서 impulse(5-3-5-3-5)·zigzag(5-3-5)
구조가 되는 모든 최상위 경계 묶음을 찾는다. 하나를 고르거나 점수를 매기지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.grammar import anchor_failures, impulse_rules
from auto_trading.research.elliott.h4_evidence import FourHourPivotEvidence

__all__ = ["Label", "Parse", "WaveParser"]


class Label(StrEnum):
    ATOM = "ATOM"
    """꼭짓점 두 개 — 이 해상도에서 내부가 안 보인다."""
    THREE = "THREE"
    """꼭짓점 네 개짜리 극단 구간 — motive 자리에서는 미검증 motive 로만 받는다."""
    IMPULSE = "IMPULSE"
    ZIGZAG = "ZIGZAG"


@dataclass(frozen=True, slots=True)
class Parse:
    """최상위 해석 하나: 경계 꼭짓점 색인과 각 하위 구간이 받은 라벨."""

    pattern: Label
    bounds: tuple[int, ...]
    leg_labels: tuple[Label, ...]

    @property
    def verified_one_level(self) -> bool:
        """모든 하위 구간이 요구 모양(비원자)으로 파싱되었는가."""
        need: tuple[Label, ...]
        if self.pattern is Label.IMPULSE:
            need = (Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE)
        else:
            need = (Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE)
        return self.leg_labels == need


class WaveParser:
    """한 ``UNKNOWN`` 구간의 연속 꼭짓점을 받는다. 교대·시간 순서는 호출 시 검사한다."""

    def __init__(
        self,
        pivots: Sequence[FourHourPivotEvidence],
        bars: Sequence[Candle],
        *,
        lenient_three: bool = True,
    ) -> None:
        if pivots and anchor_failures(list(pivots), 1 if pivots[0].kind is SwingKind.LOW else -1):
            raise DataIntegrityError("꼭짓점 열이 교대·시간 순서를 어긴다")
        self._p = tuple(pivots)
        self._bars = tuple(bars)
        self._lenient = lenient_three
        self._ends = [self._extremal_ends(i) for i in range(len(self._p))]
        self.labels = cache(self._labels)
        self.impulses = cache(self._impulses)
        self.zigzags = cache(self._zigzags)

    # ------------------------------------------------------------ 기초

    def _direction(self, i: int) -> int:
        return 1 if self._p[i].kind is SwingKind.LOW else -1

    def _extremal_ends(self, i: int) -> tuple[int, ...]:
        """p_i 가 최저(상승 기준)로 남아 있고 p_j 가 새 최고인 j 들."""
        d = self._direction(i)
        start = d * self._p[i].price
        best = start
        ends: list[int] = []
        for j in range(i + 1, len(self._p)):
            q = d * self._p[j].price
            if q <= start:
                break
            if q > best:
                best = q
                if (j - i) % 2 == 1:
                    ends.append(j)
        return tuple(ends)

    def extremal(self, i: int, j: int) -> bool:
        return j in self._ends[i]

    def _motive_ok(self, i: int, j: int) -> Label | None:
        labels = self.labels(i, j)
        if Label.IMPULSE in labels:
            return Label.IMPULSE
        if Label.ATOM in labels:
            return Label.ATOM
        if self._lenient and j - i == 3 and self.extremal(i, j):
            return Label.THREE
        return None

    def _corrective_ok(self, i: int, j: int) -> Label | None:
        labels = self.labels(i, j)
        if Label.ZIGZAG in labels:
            return Label.ZIGZAG
        if Label.ATOM in labels:
            return Label.ATOM
        return None

    # ------------------------------------------------------------ 문법

    def _labels(self, i: int, j: int) -> frozenset[Label]:
        if j == i + 1:
            return frozenset({Label.ATOM})
        if not self.extremal(i, j):
            return frozenset()
        out: set[Label] = set()
        if self.impulses(i, j):
            out.add(Label.IMPULSE)
        if self.zigzags(i, j):
            out.add(Label.ZIGZAG)
        return frozenset(out)

    def _impulses(self, i: int, j: int) -> tuple[Parse, ...]:
        if j - i < 5 or not self.extremal(i, j):
            return ()
        d = self._direction(i)
        q = [d * pivot.price for pivot in self._p]
        as_of = self._p[j].confirmed_four_hour_close_ms
        closed = tuple(bar for bar in self._bars if bar.close_ms <= as_of)
        found: list[Parse] = []
        for b1 in self._ends[i]:
            if b1 >= j - 3:
                break
            l1 = self._motive_ok(i, b1)
            if l1 is None:
                continue
            for b2 in self._ends[b1]:
                if b2 >= j - 2:
                    break
                if q[b2] <= q[i]:
                    continue
                l2 = self._corrective_ok(b1, b2)
                if l2 is None:
                    continue
                for b3 in self._ends[b2]:
                    if b3 >= j - 1:
                        break
                    if q[b3] <= q[b1]:
                        continue
                    l3 = self._motive_ok(b2, b3)
                    if l3 is None:
                        continue
                    for b4 in self._ends[b3]:
                        if b4 >= j:
                            break
                        if q[b4] <= q[b1]:
                            continue
                        l4 = self._corrective_ok(b3, b4)
                        l5 = self._motive_ok(b4, j)
                        if l4 is None or l5 is None:
                            continue
                        anchors = [self._p[k] for k in (i, b1, b2, b3, b4, j)]
                        failures, _ = impulse_rules(anchors, closed, d)
                        if failures:
                            continue
                        found.append(
                            Parse(Label.IMPULSE, (i, b1, b2, b3, b4, j), (l1, l2, l3, l4, l5))
                        )
        return tuple(found)

    def _zigzags(self, i: int, j: int) -> tuple[Parse, ...]:
        if j - i < 3 or not self.extremal(i, j):
            return ()
        found: list[Parse] = []
        for b1 in self._ends[i]:
            if b1 >= j - 1:
                break
            la = self._motive_ok(i, b1)
            if la is None:
                continue
            for b2 in self._ends[b1]:
                if b2 >= j:
                    break
                lb = self._corrective_ok(b1, b2)
                lc = self._motive_ok(b2, j)
                if lb is None or lc is None:
                    continue
                found.append(Parse(Label.ZIGZAG, (i, b1, b2, j), (la, lb, lc)))
        return tuple(found)
