"""4h 증거의 고정 rolling 창에서 최대 세 문법 가설만 만든다.

하위 degree 증거(33 §16)를 주면 각 다리의 하위 구조를 읽어 붙이고, 주지 않으면
하위파는 미검증으로 남는다. 이 출력은 연구용 분석 기록이며 주문·손절·확률이 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.degrees import (
    ChildLevel,
    ChildPath,
    check_aligned,
    leg_proof,
    read_leg,
    read_leg_descending,
    read_leg_on_path,
)
from auto_trading.research.elliott.grammar import (
    GrammarAssessment,
    GrammarScenario,
    GrammarStatus,
    LegSubdivisionProof,
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
    degree_id: str = "EW0"
    child_degree_id: str | None = None


def generate_bounded_candidates(
    four_hour_candles: Sequence[Candle],
    evidence: Sequence[FourHourEvidence],
    as_of_ms: int,
    *,
    degree_id: str = "EW0",
    child_evidence: Sequence[FourHourEvidence] | None = None,
    child_degree_id: str | None = None,
    child_path: ChildPath | None = None,
    child_levels: Sequence[ChildLevel] | None = None,
) -> CandidateSnapshot:
    """최신 연속 구간의 마지막 N개 후보만 쓰며, 패턴당 가설은 하나다.

    N=6(impulse/ending), N=4(zigzag). 중간 후보 선택·생략이나
    성과에 따른 검색은 하지 않는다. UNKNOWN은 새 구간 경계다.
    ``child_evidence`` 는 바로 아래 degree 의 같은 4h 격자 증거다. ``child_path`` 를 주면
    다리 양 끝을 그 degree 의 1m 경로 사건으로 찾는 EW-1 읽기(33 §18)를 쓴다.
    """
    if child_path is not None and child_evidence is None:
        raise DataIntegrityError("EW-1 읽기에는 하위 degree 4h 증거도 필요하다")
    if child_levels is not None and (child_evidence is not None or child_degree_id is None):
        raise DataIntegrityError("EW-2 하강 읽기는 단계 목록과 라벨(child_degree_id)만 받는다")
    if child_levels is None and (child_evidence is None) != (child_degree_id is None):
        raise DataIntegrityError("하위 degree 증거와 ID 는 함께 줘야 한다")
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
            as_of_ms, (), last_unknown_close_ms, len(candidate_indices), "INSUFFICIENT_ANCHORS",
            degree_id=degree_id, child_degree_id=child_degree_id,
        )  # fmt: skip
    child_prefix: tuple[FourHourEvidence, ...] | None = None
    if child_evidence is not None:
        child_prefix = tuple(item for item in child_evidence if item.close_ms <= as_of_ms)
        check_aligned(closed, child_prefix)
    levels: tuple[ChildLevel, ...] = ()
    if child_levels is not None:
        levels = tuple(
            ChildLevel(
                level.degree_id,
                tuple(item for item in level.evidence if item.close_ms <= as_of_ms),
                level.path,
            )
            for level in child_levels
        )
        for level in levels:
            check_aligned(closed, level.evidence)

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
        proofs: tuple[LegSubdivisionProof, ...] = ()
        if levels and child_degree_id is not None:
            legs = [item.candidate for item in window if item.candidate is not None]
            pairs = list(pairwise(legs))
            readings = [read_leg_descending(closed, levels, a, b, as_of_ms) for a, b in pairs]
            proofs = tuple(
                leg_proof(r, a, b, f"{r.child_degree_id}/{child_degree_id}")
                for r, (a, b) in zip(readings, pairs, strict=True)
            )
        elif child_prefix is not None and child_degree_id is not None:
            legs = [item.candidate for item in window if item.candidate is not None]
            if child_path is None:
                proofs = tuple(
                    leg_proof(read_leg(closed, child_prefix, a, b, as_of_ms), a, b, child_degree_id)
                    for a, b in pairwise(legs)
                )
            else:
                proofs = tuple(
                    leg_proof(
                        read_leg_on_path(closed, child_prefix, child_path, a, b, as_of_ms),
                        a,
                        b,
                        f"{child_degree_id}/EW1",
                    )
                    for a, b in pairwise(legs)
                )
        scenarios.append(
            GrammarScenario(
                scenario_id=f"{degree_id}:{pattern.value}:{window[0].close_ms}:{as_of_ms}",
                pattern=pattern,
                direction=direction,
                evidence=window,
                subdivisions=proofs,
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
        as_of_ms, tuple(records), last_unknown_close_ms, len(candidate_indices), None,
        degree_id=degree_id, child_degree_id=child_degree_id,
    )  # fmt: skip
