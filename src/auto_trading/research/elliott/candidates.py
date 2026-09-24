"""4h 증거의 고정 rolling 창에서 최대 세 문법 가설만 만든다.

시장 하위파 parser가 없으므로 생성 가설에는 증명된 subdivision이 없다.
이 출력은 연구용 분석 기록이며 주문·손절·확률이 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.grammar import (
    GrammarAssessment,
    GrammarScenario,
    GrammarStatus,
    WavePattern,
    validate_scenarios,
)
from auto_trading.research.elliott.h4_evidence import EvidenceStatus, FourHourEvidence
from auto_trading.research.elliott.pivots import closed_ew0_candles


class HypothesisStatus(StrEnum):
    FORMING = "FORMING"
    ENDING_UNVERIFIED = "ENDING_UNVERIFIED"
    ENDING_INVALIDATED = "ENDING_INVALIDATED"
    GRAMMAR_REJECTED = "GRAMMAR_REJECTED"


@dataclass(frozen=True, slots=True)
class AnalysisInvalidation:
    """완성된 종료 가설의 재연장 경계. 거래소 stop이 아니다."""

    endpoint_price: Decimal
    extension_direction: int
    invalidated_at_ms: int | None
    purpose: str = "ANALYSIS_ONLY"


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    scenario: GrammarScenario
    assessment: GrammarAssessment
    hypothesis_status: HypothesisStatus
    invalidation: AnalysisInvalidation | None


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    as_of_ms: int
    records: tuple[CandidateRecord, ...]
    last_unknown_close_ms: int | None
    usable_anchor_count: int
    reason: str | None
    confidence: str = "UNCALIBRATED_RULE_SUPPORT_ONLY"
    probability: None = None


def generate_bounded_candidates(
    four_hour_candles: Sequence[Candle],
    evidence: Sequence[FourHourEvidence],
    as_of_ms: int,
) -> CandidateSnapshot:
    """최신 연속 구간의 마지막 N개 후보만 쓰며, 패턴당 가설은 하나다.

    N=6(impulse/ending), N=4(zigzag). 중간 후보 선택·생략이나
    성과에 따른 검색은 하지 않는다. UNKNOWN은 새 구간 경계다.
    """
    closed = closed_ew0_candles(four_hour_candles, as_of_ms)
    if not closed or closed[-1].close_ms != as_of_ms:
        raise DataIntegrityError("마지막 완성 4h 마감에서만 후보를 생성한다")
    prefix = tuple(item for item in evidence if item.close_ms <= as_of_ms)
    if len(prefix) != len(closed) or any(
        item.close_ms != bar.close_ms for item, bar in zip(prefix, closed, strict=True)
    ):
        raise DataIntegrityError("4h 증거가 마감봉과 일대일 연속 대응하지 않는다")
    unknown_index = max(
        (index for index, item in enumerate(prefix) if item.status is EvidenceStatus.UNKNOWN),
        default=-1,
    )
    last_unknown_close_ms = prefix[unknown_index].close_ms if unknown_index >= 0 else None
    segment = prefix[unknown_index + 1 :]
    if any(
        item.status is EvidenceStatus.CANDIDATE
        and (
            item.candidate is None
            or (
                last_unknown_close_ms is not None
                and item.candidate.extreme_four_hour_open_ms <= last_unknown_close_ms
            )
        )
        for item in segment
    ):
        raise DataIntegrityError("UNKNOWN 경계 전 극값을 새 후보 구간에 연결할 수 없다")
    candidate_indices = [
        index for index, item in enumerate(segment) if item.status is EvidenceStatus.CANDIDATE
    ]
    if len(candidate_indices) < 2:
        return CandidateSnapshot(
            as_of_ms, (), last_unknown_close_ms, len(candidate_indices), "INSUFFICIENT_ANCHORS"
        )

    scenarios: list[GrammarScenario] = []
    for pattern, required in (
        (WavePattern.IMPULSE, 6),
        (WavePattern.ZIGZAG, 4),
        (WavePattern.ENDING_DIAGONAL, 6),
    ):
        selected = candidate_indices[-required:]
        window = segment[selected[0] :]
        first = window[0].candidate
        if first is None:
            raise DataIntegrityError("CANDIDATE의 피벗 증거가 없다")
        direction = 1 if first.kind is SwingKind.LOW else -1
        scenarios.append(
            GrammarScenario(
                scenario_id=f"EW0:{pattern.value}:{window[0].close_ms}:{as_of_ms}",
                pattern=pattern,
                direction=direction,
                evidence=window,
                subdivisions=(),
            )
        )
    assessments = validate_scenarios(closed, scenarios, as_of_ms)
    records: list[CandidateRecord] = []
    for scenario, assessment in zip(scenarios, assessments, strict=True):
        required = 4 if scenario.pattern is WavePattern.ZIGZAG else 6
        anchors = [
            item.candidate
            for item in scenario.evidence
            if item.status is EvidenceStatus.CANDIDATE and item.candidate is not None
        ]
        invalidation: AnalysisInvalidation | None = None
        if assessment.geometry_status is GrammarStatus.FAIL:
            state = HypothesisStatus.GRAMMAR_REJECTED
        elif len(anchors) < required or assessment.geometry_status is GrammarStatus.PENDING:
            state = HypothesisStatus.FORMING
        else:
            endpoint = anchors[-1]
            invalidated_at_ms = next(
                (
                    bar.close_ms
                    for bar in closed
                    if bar.close_ms > endpoint.confirmed_four_hour_close_ms
                    and (
                        bar.high > endpoint.price
                        if scenario.direction > 0
                        else bar.low < endpoint.price
                    )
                ),
                None,
            )
            invalidation = AnalysisInvalidation(
                endpoint.price, scenario.direction, invalidated_at_ms
            )
            state = (
                HypothesisStatus.ENDING_INVALIDATED
                if invalidated_at_ms is not None
                else HypothesisStatus.ENDING_UNVERIFIED
            )
        records.append(CandidateRecord(scenario, assessment, state, invalidation))
    return CandidateSnapshot(
        as_of_ms, tuple(records), last_unknown_close_ms, len(candidate_indices), None
    )
