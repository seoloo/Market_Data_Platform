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
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT

__all__ = [
    "DEGREE_LADDER",
    "ChildReadingReason",
    "DegreeSpec",
    "LegReading",
    "check_aligned",
    "leg_proof",
    "read_leg",
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

    direction = 1 if start.kind is SwingKind.LOW else -1
    if anchor_failures(children, direction):
        raise DataIntegrityError("하위 degree 후보가 교대·시간 순서를 어긴다")
    available = max(end.confirmed_four_hour_close_ms, bars[end_index].close_ms)
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
