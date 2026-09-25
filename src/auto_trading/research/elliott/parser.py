"""EW-3: 4h 꼭짓점 열을 Elliott 문법으로 파싱해 가능한 모든 해석을 낸다 (33 §24, 순수 함수).

degree 를 고정 % 임계로 정하지 않는다. 가장 촘촘한 꼭짓점 열에서 impulse(5-3-5-3-5)·zigzag(5-3-5)
구조가 되는 모든 최상위 경계 묶음을 찾는다. 하나를 고르거나 점수를 매기지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from functools import cache
from itertools import pairwise

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.grammar import impulse_rules
from auto_trading.research.elliott.h4_evidence import FourHourPivotEvidence
from auto_trading.research.elliott.minute_path import MinutePathReplay

__all__ = [
    "FLAT_B_MAX",
    "FLAT_B_MIN",
    "FLAT_C_MAX",
    "FLAT_C_MIN",
    "LEG_NEEDS",
    "FinePivot",
    "Label",
    "Parse",
    "WaveParser",
    "as_grid_anchor",
    "counted_at_four_hour",
    "four_hour_path_pivots",
    "hourly_path_pivots",
]


class Label(StrEnum):
    ATOM = "ATOM"
    """꼭짓점 두 개 — 이 해상도에서 내부가 안 보인다."""
    THREE = "THREE"
    """꼭짓점 네 개짜리 극단 구간 — motive 자리에서는 미검증 motive 로만 받는다."""
    IMPULSE = "IMPULSE"
    ZIGZAG = "ZIGZAG"
    OPAQUE = "OPAQUE"
    """EW-3d: 극단 구간이지만 요구 모양으로 파싱되지 않은 다리 — 후보는 유지, 미검증."""
    FLAT = "FLAT"
    """EW-4 (문서 60): 플랫 A-B-C (3-3-5) — 최상위 후보로만."""
    TRIANGLE = "TRIANGLE"
    """EW-4 (문서 60): 수렴 삼각형 A-B-C-D-E (3-3-3-3-3) — 최상위 후보로만."""


LEG_NEEDS: dict[Label, tuple[Label, ...]] = {}
"""패턴별 다리가 읽혀야 할 모양 (아래에서 채운다)."""


@dataclass(frozen=True, slots=True)
class Parse:
    """최상위 해석 하나: 경계 꼭짓점 색인과 각 하위 구간이 받은 라벨."""

    pattern: Label
    bounds: tuple[int, ...]
    leg_labels: tuple[Label, ...]

    @property
    def verified_one_level(self) -> bool:
        """모든 하위 구간이 요구 모양(비원자)으로 파싱되었는가."""
        return self.leg_labels == LEG_NEEDS[self.pattern]


LEG_NEEDS.update(
    {
        Label.IMPULSE: (Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE),
        Label.ZIGZAG: (Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE),
        Label.FLAT: (Label.ZIGZAG, Label.ZIGZAG, Label.IMPULSE),
        Label.TRIANGLE: (Label.ZIGZAG,) * 5,
    }
)

FLAT_B_MIN, FLAT_B_MAX = 0.618, 1.618
"""문서 60 §3.1 F1·F2: B/A 범위."""
FLAT_C_MIN, FLAT_C_MAX = 0.382, 2.618
"""문서 60 §3.1 F3: C/A 범위."""


class WaveParser:
    """한 ``UNKNOWN`` 구간의 연속 꼭짓점을 받는다. 교대·시간 순서는 호출 시 검사한다."""

    def __init__(
        self,
        pivots: Sequence[FourHourPivotEvidence],
        bars: Sequence[Candle],
        *,
        lenient_three: bool = True,
    ) -> None:
        for a, b in pairwise(pivots):
            if (
                a.kind is b.kind
                or b.extreme_four_hour_open_ms <= a.extreme_four_hour_open_ms
                or b.confirmed_four_hour_close_ms < a.confirmed_four_hour_close_ms
            ):
                raise DataIntegrityError("꼭짓점 열이 교대·시간 순서를 어긴다")
        self._p = tuple(pivots)
        self._bars = tuple(bars)
        self._lenient = lenient_three
        self._ends = [self._extremal_ends(i) for i in range(len(self._p))]
        self._relaxed = [self._start_ends(i) for i in range(len(self._p))]
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

    def relaxed_span(self, i: int, j: int) -> bool:
        """시작 극단(i 를 넘어 되돌아가는 꼭짓점이 없음) 구간인가 — EW-3d′ 후보의 전체 구간 조건."""
        return j in self._relaxed[i]

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

    # ------------------------------------------------------------ EW-3d 후보 (33 §28)

    def _start_ends(self, i: int) -> tuple[int, ...]:
        """EW-3d′: p_i 를 넘어 되돌아가는 꼭짓점이 나오기 전까지의 j (끝 극단은 요구하지 않음)."""
        d = self._direction(i)
        start = d * self._p[i].price
        ends: list[int] = []
        for j in range(i + 1, len(self._p)):
            if d * self._p[j].price <= start:
                break
            if (j - i) % 2 == 1:
                ends.append(j)
        return tuple(ends)

    def _leg(self, i: int, j: int, need: Label, *, relaxed: bool = False) -> Label | None:
        """후보용 다리: 원자·(시작) 극단 구간이면 받고, 요구 모양 파싱 여부만 라벨로 남긴다."""
        if j == i + 1:
            return Label.ATOM
        if not (j in self._relaxed[i] if relaxed else self.extremal(i, j)):
            return None
        return need if need in self.labels(i, j) else Label.OPAQUE

    def top_candidates(self, i: int, j: int, *, relaxed: bool = False) -> tuple[Parse, ...]:
        """절대 규칙만으로 만든 최상위 후보 전부 (impulse·zigzag). 내부 미검증 다리도 포함.

        ``relaxed`` (33 §28 변경 1): 다리·전체 구간에 시작 극단만 요구해 절단형(5파·C)을 받는다.
        """
        ends = self._relaxed if relaxed else self._ends
        if j not in ends[i]:
            return ()
        d = self._direction(i)
        q = [d * pivot.price for pivot in self._p]
        as_of = self._p[j].confirmed_four_hour_close_ms
        closed = tuple(bar for bar in self._bars if bar.close_ms <= as_of)
        found: list[Parse] = []
        for b1 in ends[i]:
            if b1 >= j:
                break
            la = self._leg(i, b1, Label.IMPULSE, relaxed=relaxed)
            if la is None:
                continue
            for b2 in ends[b1]:
                if b2 >= j:
                    break
                lb = self._leg(b1, b2, Label.ZIGZAG, relaxed=relaxed)
                if lb is None:
                    continue
                lc = self._leg(b2, j, Label.IMPULSE, relaxed=relaxed)
                if lc is not None and q[j] > q[b1] and q[b2] > q[i]:
                    found.append(Parse(Label.ZIGZAG, (i, b1, b2, j), (la, lb, lc)))
                if q[b2] <= q[i]:
                    continue
                for b3 in ends[b2]:
                    if b3 >= j - 1:
                        break
                    if q[b3] <= q[b1]:
                        continue
                    l3 = self._leg(b2, b3, Label.IMPULSE, relaxed=relaxed)
                    if l3 is None:
                        continue
                    for b4 in ends[b3]:
                        if b4 >= j:
                            break
                        if q[b4] <= q[b1]:
                            continue
                        l4 = self._leg(b3, b4, Label.ZIGZAG, relaxed=relaxed)
                        l5 = self._leg(b4, j, Label.IMPULSE, relaxed=relaxed)
                        if l4 is None or l5 is None:
                            continue
                        anchors = [self._p[k] for k in (i, b1, b2, b3, b4, j)]
                        if impulse_rules(anchors, closed, d)[0]:
                            continue
                        found.append(
                            Parse(Label.IMPULSE, (i, b1, b2, b3, b4, j), (la, lb, l3, l4, l5))
                        )
        return tuple(found)

    def pattern_candidates(self, i: int, j: int) -> tuple[Parse, ...]:
        """EW-4 1단계 (문서 60 §3): 플랫·수렴 삼각형 최상위 후보. 각 다리는 시작 극단(relaxed).

        전체 구간이 시작 극단일 필요는 없다 — 확장 플랫은 B 가 A 시작을 넘는다.
        """
        d = self._direction(i)
        q = [d * float(pivot.price) for pivot in self._p]
        rel = self._relaxed
        found: list[Parse] = []
        for b1 in rel[i]:
            if b1 >= j:
                break
            a = q[b1] - q[i]
            if a <= 0:
                continue
            la = self._leg(i, b1, Label.ZIGZAG, relaxed=True)
            if la is None:
                continue
            for b2 in rel[b1]:
                if b2 >= j:
                    break
                b = q[b1] - q[b2]
                lb = self._leg(b1, b2, Label.ZIGZAG, relaxed=True)
                if lb is None or b <= 0:
                    continue
                # 플랫: C = b2 → j
                if j in rel[b2] and FLAT_B_MIN <= b / a <= FLAT_B_MAX:
                    c = q[j] - q[b2]
                    lc = self._leg(b2, j, Label.IMPULSE, relaxed=True)
                    if lc is not None and FLAT_C_MIN <= c / a <= FLAT_C_MAX:
                        found.append(Parse(Label.FLAT, (i, b1, b2, j), (la, lb, lc)))
                # 수렴 삼각형: B 는 A 시작을 넘지 않는다
                if q[b2] <= q[i]:
                    continue
                for b3 in rel[b2]:
                    if b3 >= j - 1:
                        break
                    if q[b3] >= q[b1]:  # C 는 A 끝 안쪽
                        continue
                    lc3 = self._leg(b2, b3, Label.ZIGZAG, relaxed=True)
                    if lc3 is None:
                        continue
                    for b4 in rel[b3]:
                        if b4 >= j:
                            break
                        if q[b4] <= q[b2]:  # D 는 B 끝 안쪽
                            continue
                        if j not in rel[b4] or q[j] >= q[b3]:  # E 는 C 끝 안쪽
                            continue
                        ld = self._leg(b3, b4, Label.ZIGZAG, relaxed=True)
                        le = self._leg(b4, j, Label.ZIGZAG, relaxed=True)
                        if ld is None or le is None:
                            continue
                        found.append(
                            Parse(Label.TRIANGLE, (i, b1, b2, b3, b4, j), (la, lb, lc3, ld, le))
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


def four_hour_path_pivots(
    replay: MinutePathReplay, bars: Sequence[Candle]
) -> tuple[tuple[FourHourPivotEvidence, ...], ...]:
    """EW-3b (33 §24 변경 1): 1m 경로 사건을 4h 해상도 꼭짓점 열로 옮긴다.

    가격이 극값 4h 봉의 high/low 와 다르거나 연속 두 사건의 극값이 같은 4h 봉이면(4h 미만 파동)
    그 자리에서 구간을 끊는다. 1m 경로 구간 경계(차단 4h·모호한 분)도 그대로 경계다.
    """
    by_open = {bar.open_ms: bar for bar in bars}
    out: list[tuple[FourHourPivotEvidence, ...]] = []
    for segment in replay.segments:
        current: list[FourHourPivotEvidence] = []
        for event in segment:
            bar_open = Timeframe.H4.open_ms_of(event.extreme_minute_ms)
            bar = by_open.get(bar_open)
            extreme = (
                None if bar is None else (bar.high if event.kind is SwingKind.HIGH else bar.low)
            )
            if extreme != event.price:
                if current:
                    out.append(tuple(current))
                current = []
                continue
            if current and current[-1].extreme_four_hour_open_ms == bar_open:
                current.pop()
                if current:
                    out.append(tuple(current))
                current = []
                continue
            current.append(
                FourHourPivotEvidence(
                    kind=event.kind,
                    price=event.price,
                    extreme_four_hour_open_ms=bar_open,
                    confirmed_four_hour_close_ms=event.available_at_ms,
                )
            )
        if current:
            out.append(tuple(current))
    return tuple(out)


# ---------------------------------------------------------------- EW-3c (33 §26)


@dataclass(frozen=True, slots=True)
class FinePivot:
    """1h 해상도 꼭짓점. 카운트(최상위)에는 ``four_hour_visible`` 인 것만 쓴다."""

    kind: SwingKind
    price: Decimal
    hour_open_ms: int
    four_hour_open_ms: int
    available_at_ms: int
    four_hour_visible: bool


def hourly_path_pivots(
    replay: MinutePathReplay, hour_bars: Sequence[Candle], four_hour_bars: Sequence[Candle]
) -> tuple[tuple[FinePivot, ...], ...]:
    """1m 경로 사건을 1h 해상도로 옮긴다. 1h 봉 극값 불일치·같은 1h 봉 연속 꼭짓점은 끊는다."""
    hours = {bar.open_ms: bar for bar in hour_bars}
    fours = {bar.open_ms: bar for bar in four_hour_bars}
    out: list[tuple[FinePivot, ...]] = []
    for segment in replay.segments:
        current: list[FinePivot] = []
        for event in segment:
            hour_open = Timeframe.H1.open_ms_of(event.extreme_minute_ms)
            four_open = Timeframe.H4.open_ms_of(event.extreme_minute_ms)
            hour, four = hours.get(hour_open), fours.get(four_open)
            if hour is None or four is None or event.price != _extreme(hour, event.kind):
                if current:
                    out.append(tuple(current))
                current = []
                continue
            if current and current[-1].hour_open_ms == hour_open:
                current.pop()
                if current:
                    out.append(tuple(current))
                current = []
                continue
            current.append(
                FinePivot(
                    kind=event.kind,
                    price=event.price,
                    hour_open_ms=hour_open,
                    four_hour_open_ms=four_open,
                    available_at_ms=event.available_at_ms,
                    four_hour_visible=event.price == _extreme(four, event.kind),
                )
            )
        if current:
            out.append(tuple(current))
    return tuple(out)


def _extreme(bar: Candle, kind: SwingKind) -> Decimal:
    return bar.high if kind is SwingKind.HIGH else bar.low


def as_grid_anchor(pivot: FinePivot) -> FourHourPivotEvidence:
    """파서·impulse 규칙 입력용.

    1h 격자 위에서 파싱하므로 극값 시각 필드에 **1h 봉 open** 을 담는다.
    """
    return FourHourPivotEvidence(
        kind=pivot.kind,
        price=pivot.price,
        extreme_four_hour_open_ms=pivot.hour_open_ms,
        confirmed_four_hour_close_ms=pivot.available_at_ms,
    )


def counted_at_four_hour(parse: Parse, pivots: Sequence[FinePivot]) -> bool:
    """최상위 경계가 모두 4h 가시이고 이웃 경계가 서로 다른 4h 봉인가 (카운트는 4h 에서)."""
    bounds = [pivots[k] for k in parse.bounds]
    return all(p.four_hour_visible for p in bounds) and all(
        a.four_hour_open_ms != b.four_hour_open_ms for a, b in pairwise(bounds)
    )
