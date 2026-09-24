"""4h degree 사다리로 부모 다리의 하위 구조를 인과적으로 읽는다 (33 §16, 순수 함수).

같은 임계의 연속 피벗 사이에는 그 임계 이상의 역행이 없으므로 한 다리의 내부 파동은
**바로 아래 degree**(임계 절반)의 4h 후보 증거로만 읽는다. 4h 미만 파동은 만들지 않고,
더 낮은 degree 로 내려가 맞는 구조를 찾지 않는다. 결과는 문법의 하위파 증거일 뿐
파동 정답·매매 신호가 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.grammar import (
    LegSubdivision,
    LegSubdivisionProof,
    ProofSource,
    anchor_failures,
    ending_diagonal_rules,
    impulse_rules,
)
from auto_trading.research.elliott.h4_evidence import (
    EvidenceStatus,
    FourHourEvidence,
    FourHourPivotEvidence,
)
from auto_trading.research.elliott.minute_path import MinutePathPivot, MinutePathReplay
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT

__all__ = [
    "DEGREE_LADDER",
    "ChildPath",
    "ChildReadingReason",
    "DegreeSpec",
    "LegReading",
    "check_aligned",
    "index_child_path",
    "leg_proof",
    "read_leg",
    "read_leg_on_path",
]


@dataclass(frozen=True, slots=True)
class DegreeSpec:
    degree_id: str
    threshold_pct: Decimal


DEGREE_LADDER: tuple[DegreeSpec, ...] = (
    DegreeSpec("D0", EW0_THRESHOLD_PCT),
    DegreeSpec("D1", Decimal("0.04")),
    DegreeSpec("D2", Decimal("0.08")),
    DegreeSpec("D3", Decimal("0.16")),
)
"""33 §16 에서 결과 열람 전 고정: 2% 부터 배율 2. 조정하려면 새 프로파일."""


class ChildReadingReason(StrEnum):
    START_NOT_ALIGNED = "START_NOT_ALIGNED"
    END_NOT_ALIGNED = "END_NOT_ALIGNED"
    CHILD_UNKNOWN_IN_LEG = "CHILD_UNKNOWN_IN_LEG"
    CHILD_SINGLE_LEG = "CHILD_SINGLE_LEG"
    CHILD_FIVE_PENDING = "CHILD_FIVE_PENDING"
    CHILD_FIVE_NOT_MOTIVE = "CHILD_FIVE_NOT_MOTIVE"
    CHILD_LEGS_7_PLUS = "CHILD_LEGS_7_PLUS"
    # EW-1 (33 §18)
    START_NOT_ON_PATH = "START_NOT_ON_PATH"
    END_NOT_ON_PATH = "END_NOT_ON_PATH"
    PATH_BROKEN_IN_LEG = "PATH_BROKEN_IN_LEG"
    CHILD_SUB_4H_AT_VERTEX = "CHILD_SUB_4H_AT_VERTEX"
    CHILD_INTERIOR_UNRESOLVED = "CHILD_INTERIOR_UNRESOLVED"
    VERTEX_CONFIRMED_SAME_4H = "VERTEX_CONFIRMED_SAME_4H"


@dataclass(frozen=True, slots=True)
class LegReading:
    shape: LegSubdivision
    child_leg_count: int | None
    """보이는 하위 다리 수. 정렬·연속 실패면 None."""
    reason: ChildReadingReason | None
    available_at_ms: int
    child_anchors: tuple[FourHourPivotEvidence, ...] = ()


def check_aligned(bars: Sequence[Candle], evidence: Sequence[FourHourEvidence]) -> None:
    """증거가 완성 4h 봉과 일대일·같은 순서인지 확인한다 (호출자가 한 번만 부르면 된다)."""
    if len(evidence) != len(bars) or any(
        item.close_ms != bar.close_ms for item, bar in zip(evidence, bars, strict=True)
    ):
        raise DataIntegrityError("하위 degree 증거가 마감 4h 봉과 일대일 대응하지 않는다")


def _same(candidate: FourHourPivotEvidence, anchor: FourHourPivotEvidence) -> bool:
    return (
        candidate.kind is anchor.kind
        and candidate.extreme_four_hour_open_ms == anchor.extreme_four_hour_open_ms
        and candidate.price == anchor.price
    )


def read_leg(
    bars: Sequence[Candle],
    child_evidence: Sequence[FourHourEvidence],
    start: FourHourPivotEvidence,
    end: FourHourPivotEvidence,
    as_of_ms: int,
) -> LegReading:
    """부모 다리 start→end 를 바로 아래 degree 증거로 읽는다.

    ``bars``·``child_evidence`` 는 같은 4h 격자에 일대일 정렬돼 있어야 한다
    (:func:`check_aligned`). ``as_of_ms`` 이후에 마감한 봉의 증거는 읽지 않는다.
    """
    if not bars or end.extreme_four_hour_open_ms <= start.extreme_four_hour_open_ms:
        raise DataIntegrityError("부모 다리의 시간 순서가 유효하지 않다")
    if end.confirmed_four_hour_close_ms > as_of_ms:
        raise DataIntegrityError("아직 확정되지 않은 부모 꼭짓점이다")
    origin = bars[0].open_ms
    fallback = end.confirmed_four_hour_close_ms

    def unknown(reason: ChildReadingReason) -> LegReading:
        return LegReading(LegSubdivision.UNKNOWN, None, reason, fallback)

    index = (start.extreme_four_hour_open_ms - origin) // Timeframe.H4.ms + 1
    child_start: FourHourPivotEvidence | None = None
    while index < len(bars) and bars[index].close_ms <= as_of_ms:
        item = child_evidence[index]
        if item.status is EvidenceStatus.CANDIDATE and item.candidate is not None:
            if _same(item.candidate, start):
                child_start = item.candidate
                break
            if item.candidate.extreme_four_hour_open_ms >= start.extreme_four_hour_open_ms:
                break
        index += 1
    if child_start is None:
        return unknown(ChildReadingReason.START_NOT_ALIGNED)
    start_index = index

    # 자식 쪽 꼭짓점(자식 확정 시각)을 쓴다 — 부모 확정은 보통 더 늦다
    children: list[FourHourPivotEvidence] = [child_start]
    end_index: int | None = None
    index = start_index + 1
    while index < len(bars) and bars[index].close_ms <= as_of_ms:
        item = child_evidence[index]
        if item.status is EvidenceStatus.UNKNOWN:
            return unknown(ChildReadingReason.CHILD_UNKNOWN_IN_LEG)
        if item.status is EvidenceStatus.CANDIDATE and item.candidate is not None:
            candidate = item.candidate
            if _same(candidate, end):
                children.append(candidate)
                end_index = index
                break
            if candidate.extreme_four_hour_open_ms >= end.extreme_four_hour_open_ms:
                return unknown(ChildReadingReason.END_NOT_ALIGNED)
            children.append(candidate)
        index += 1
    if end_index is None:
        return unknown(ChildReadingReason.END_NOT_ALIGNED)

    available = max(end.confirmed_four_hour_close_ms, bars[end_index].close_ms)
    return _classify(bars, children, start, available, as_of_ms)


def _classify(
    bars: Sequence[Candle],
    children: list[FourHourPivotEvidence],
    start: FourHourPivotEvidence,
    available: int,
    as_of_ms: int,
) -> LegReading:
    """보이는 하위 다리 수로 모양을 정한다 (33 §16 의 n 규칙, EW-0·EW-1 공통)."""
    direction = 1 if start.kind is SwingKind.LOW else -1
    if anchor_failures(children, direction):
        raise DataIntegrityError("하위 degree 후보가 교대·시간 순서를 어긴다")
    count = len(children) - 1

    def reading(shape: LegSubdivision, reason: ChildReadingReason | None = None) -> LegReading:
        return LegReading(shape, count, reason, available, tuple(children))

    if count == 1:
        return reading(LegSubdivision.UNKNOWN, ChildReadingReason.CHILD_SINGLE_LEG)
    if count == 3:
        return reading(LegSubdivision.CORRECTIVE_THREE)
    if count == 5:
        closed = tuple(bar for bar in bars if bar.close_ms <= as_of_ms)
        impulse_fail, impulse_pending = impulse_rules(children, closed, direction)
        if not impulse_fail and not impulse_pending:
            return reading(LegSubdivision.IMPULSE_FIVE)
        diagonal_fail, _ = ending_diagonal_rules(children, direction)
        if not diagonal_fail:
            return reading(LegSubdivision.MOTIVE_FIVE)
        if not impulse_fail:
            return reading(LegSubdivision.UNKNOWN, ChildReadingReason.CHILD_FIVE_PENDING)
        return reading(LegSubdivision.UNKNOWN, ChildReadingReason.CHILD_FIVE_NOT_MOTIVE)
    return reading(LegSubdivision.UNKNOWN, ChildReadingReason.CHILD_LEGS_7_PLUS)


# ---------------------------------------------------------------- EW-1 (33 §18)

_PathKey = tuple[SwingKind, int, Decimal]


@dataclass(frozen=True, slots=True)
class ChildPath:
    """자식 degree 1m 경로 사건의 조회표. 전체 기간 재생이어도 가용 시각으로 인과를 지킨다."""

    replay: MinutePathReplay
    positions: dict[_PathKey, tuple[tuple[int, int], ...]]


def index_child_path(replay: MinutePathReplay) -> ChildPath:
    positions: dict[_PathKey, list[tuple[int, int]]] = {}
    for s, segment in enumerate(replay.segments):
        for i, event in enumerate(segment):
            key = (event.kind, Timeframe.H4.open_ms_of(event.extreme_minute_ms), event.price)
            positions.setdefault(key, []).append((s, i))
    return ChildPath(replay, {k: tuple(v) for k, v in positions.items()})


def _path_vertex(
    path: ChildPath, anchor: FourHourPivotEvidence, as_of_ms: int
) -> tuple[int, int] | None:
    key = (anchor.kind, anchor.extreme_four_hour_open_ms, anchor.price)
    found = [
        (s, i)
        for s, i in path.positions.get(key, ())
        if path.replay.segments[s][i].available_at_ms <= as_of_ms
    ]
    return found[0] if len(found) == 1 else None


def _as_vertex(event: MinutePathPivot) -> FourHourPivotEvidence:
    return FourHourPivotEvidence(
        kind=event.kind,
        price=event.price,
        extreme_four_hour_open_ms=Timeframe.H4.open_ms_of(event.extreme_minute_ms),
        confirmed_four_hour_close_ms=event.available_at_ms,
    )


def read_leg_on_path(
    bars: Sequence[Candle],
    child_evidence: Sequence[FourHourEvidence],
    child_path: ChildPath,
    start: FourHourPivotEvidence,
    end: FourHourPivotEvidence,
    as_of_ms: int,
) -> LegReading:
    """EW-1: 양 끝 꼭짓점만 자식 degree 1m 경로 사건으로 찾고, 내부는 4h 후보만 인정한다."""
    if not bars or end.extreme_four_hour_open_ms <= start.extreme_four_hour_open_ms:
        raise DataIntegrityError("부모 다리의 시간 순서가 유효하지 않다")
    if end.confirmed_four_hour_close_ms > as_of_ms:
        raise DataIntegrityError("아직 확정되지 않은 부모 꼭짓점이다")
    fallback = end.confirmed_four_hour_close_ms

    def unknown(reason: ChildReadingReason) -> LegReading:
        return LegReading(LegSubdivision.UNKNOWN, None, reason, fallback)

    first = _path_vertex(child_path, start, as_of_ms)
    if first is None:
        return unknown(ChildReadingReason.START_NOT_ON_PATH)
    last = _path_vertex(child_path, end, as_of_ms)
    if last is None:
        return unknown(ChildReadingReason.END_NOT_ON_PATH)
    if first[0] != last[0] or last[1] <= first[1]:
        return unknown(ChildReadingReason.PATH_BROKEN_IN_LEG)
    segment = child_path.replay.segments[first[0]]
    first_close = bars[0].close_ms
    children = [_as_vertex(segment[first[1]])]
    for event in segment[first[1] + 1 : last[1]]:
        vertex = _as_vertex(event)
        if not (
            start.extreme_four_hour_open_ms
            < vertex.extreme_four_hour_open_ms
            < end.extreme_four_hour_open_ms
        ):
            return unknown(ChildReadingReason.CHILD_SUB_4H_AT_VERTEX)
        index = (vertex.confirmed_four_hour_close_ms - first_close) // Timeframe.H4.ms
        item = child_evidence[index] if 0 <= index < len(child_evidence) else None
        if item is None or item.status is not EvidenceStatus.CANDIDATE or item.candidate != vertex:
            return unknown(ChildReadingReason.CHILD_INTERIOR_UNRESOLVED)
        children.append(vertex)
    end_vertex = _as_vertex(segment[last[1]])
    children.append(end_vertex)
    if any(
        later.confirmed_four_hour_close_ms <= earlier.confirmed_four_hour_close_ms
        for earlier, later in pairwise(children)
    ):
        # 연속 꼭짓점의 반전이 같은 4h 안에서 확인됨 — 4h 해상도로 순서를 세울 수 없다
        return unknown(ChildReadingReason.VERTEX_CONFIRMED_SAME_4H)
    available = max(end.confirmed_four_hour_close_ms, end_vertex.confirmed_four_hour_close_ms)
    return _classify(bars, children, start, available, as_of_ms)


def leg_proof(
    reading: LegReading,
    start: FourHourPivotEvidence,
    end: FourHourPivotEvidence,
    child_degree_id: str,
) -> LegSubdivisionProof:
    """문법 입력용 하위파 증거로 옮긴다 (출처 ``H4_DEGREE_LADDER``)."""
    return LegSubdivisionProof(
        shape=reading.shape,
        start_four_hour_open_ms=start.extreme_four_hour_open_ms,
        end_four_hour_open_ms=end.extreme_four_hour_open_ms,
        available_at_ms=reading.available_at_ms,
        timeframe=Timeframe.H4,
        source=ProofSource.H4_DEGREE_LADDER,
        source_id=(
            f"{child_degree_id}:{start.extreme_four_hour_open_ms}->{end.extreme_four_hour_open_ms}"
        ),
        child_leg_count=reading.child_leg_count,
        reason=None if reading.reason is None else reading.reason.value,
    )
