"""Elliott 복수 시나리오 출력 계약 — 나중에 JEV 같은 평가자가 읽을 입력 (순수, I/O 없음).

ELLIOTT_WAVE_ANALYSIS §6.3·§11 참조. 원칙:

* **하나의 count 를 확정하지 않는다.** 시나리오 묶음은 항상 ``exhaustive=False`` 이고
  ``other_or_unknown`` 자리를 따로 둔다 — 제한된 탐색이라 목록 밖의 해석이 늘 가능하다.
* 신뢰도는 **확률이 아니다.** ``RuleSupport`` 는 규칙 통과/보류/실패 수와 가이드라인 부합 수의
  원값만 담고 ``probability`` 는 항상 ``None`` 이다. 가중 합산 점수를 만들지 않는다.
* 무효화 가격은 **분석 전용**(``ANALYSIS_ONLY``)이다. 주문·손절·veto 에 쓰지 않는다.
* 아직 계산하지 않은 근거는 ``NOT_COMPUTED`` 로 적는다 — 없는 근거를 중립값으로 위장하지 않는다.

평가자 쪽 계약은 :mod:`auto_trading.research.elliott.evaluation` 에 있다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from auto_trading.research.elliott.candidates import (
    CandidateRecord,
    CandidateSnapshot,
    HypothesisStatus,
)
from auto_trading.research.elliott.grammar import (
    EXPECTED_SUBDIVISIONS,
    GrammarStatus,
    LegSubdivision,
    ProofSource,
    WavePattern,
    subdivision_matches,
)
from auto_trading.research.elliott.h4_evidence import EvidenceStatus, FourHourPivotEvidence

__all__ = [
    "SCHEMA_VERSION",
    "Anchor",
    "CheckStatus",
    "EvidenceItem",
    "EvidenceState",
    "HardCheck",
    "InvalidationLevel",
    "RuleSupport",
    "ScenarioSet",
    "SetStatus",
    "Uncertainty",
    "WaveScenario",
    "from_candidate_snapshot",
    "scenario_set_digest",
    "to_json_dict",
    "validate_scenario_set_dict",
]

SCHEMA_VERSION = "ew-scenario-set/1"
_H4_MS = 4 * 3_600_000


class SetStatus(StrEnum):
    SCENARIOS = "SCENARIOS"
    """시나리오가 있다 (전부 기각이어도 기각 사유와 함께 싣는다)."""
    NO_VALID_COUNT = "NO_VALID_COUNT"
    """시나리오는 만들었지만 모두 규칙 위반."""
    INSUFFICIENT_ANCHORS = "INSUFFICIENT_ANCHORS"
    UNKNOWN = "UNKNOWN"
    """자료 품질·경계 문제로 판단 불가."""


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    UNVERIFIED = "UNVERIFIED"


class EvidenceState(StrEnum):
    OBSERVED = "OBSERVED"
    NOT_COMPUTED = "NOT_COMPUTED"
    """이 버전에서 계산하지 않는다 — 중립이 아니다."""
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_ANCHORS = "INSUFFICIENT_ANCHORS"


@dataclass(frozen=True, slots=True)
class Anchor:
    label: str
    """파동 번호: impulse/ending ``0..5``, zigzag ``0,A,B,C``."""
    price: str
    extreme_at_ms: int
    """극값 4h 봉 open."""
    confirmed_at_ms: int
    """확정 4h 봉 close — 이 시각 전에는 알 수 없었다."""
    provisional: bool = False


@dataclass(frozen=True, slots=True)
class HardCheck:
    rule_id: str
    status: CheckStatus
    layer: str = "HARD_RULE"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    family: str
    """FIBONACCI · ALTERNATION · MOMENTUM · VOLUME · MULTI_TIMEFRAME · CHANNEL."""
    key: str
    state: EvidenceState
    observed: float | None = None
    reference_band: tuple[float, float] | None = None
    """가이드라인 범위 (출처 ``source``). None 이면 범위 없이 관찰값만."""
    in_band: bool | None = None
    source: str = ""
    layer: str = "GUIDELINE"
    """HARD_RULE · GUIDELINE · SUBJECTIVE (ELLIOTT_WAVE_ANALYSIS §3.2)."""


@dataclass(frozen=True, slots=True)
class InvalidationLevel:
    rule_id: str
    price: str
    comparator: str
    """``<=`` 또는 ``>=`` — 이 가격에 닿으면 해당 시나리오(또는 종료 주장)가 무효."""
    price_basis: str
    """HIGH · LOW · CLOSE."""
    triggered_at_ms: int | None = None
    scope: str = "ANALYSIS_ONLY"


@dataclass(frozen=True, slots=True)
class Uncertainty:
    subdivision_status: str
    """VERIFIED · PARTIAL · UNVERIFIED — 하위파 실측 검증 여부."""
    competing_scenarios: int
    flags: tuple[str, ...]
    other_or_unknown_possible: bool = True
    """항상 True — 이 목록은 완전하지 않다."""


@dataclass(frozen=True, slots=True)
class RuleSupport:
    """확률이 아닌 규칙 지지 원값. 평가자는 이것을 확률로 해석하면 안 된다."""

    hard_pass: int
    hard_fail: int
    hard_pending: int
    guideline_in_band: int
    guideline_evaluated: int
    kind: str = "RULE_SUPPORT_UNCALIBRATED"
    probability: None = None


@dataclass(frozen=True, slots=True)
class WaveScenario:
    scenario_id: str
    pattern: str
    direction: int
    """+1 상승 구조, -1 하락 구조 (구조의 방향이지 매매 방향이 아니다)."""
    degree_id: str
    parent_id: str | None
    wave_count: tuple[str, ...]
    """완성 시 전체 번호 (예: 0,1,2,3,4,5)."""
    current_state: str
    """예: ``WAVE_4_FORMING`` · ``WAVE_5_END_UNVERIFIED`` · ``END_INVALIDATED`` · ``REJECTED``."""
    hypothesis_status: str
    anchors: tuple[Anchor, ...]
    hard_checks: tuple[HardCheck, ...]
    evidence: tuple[EvidenceItem, ...]
    invalidation_levels: tuple[InvalidationLevel, ...]
    uncertainty: Uncertainty
    rule_support: RuleSupport


@dataclass(frozen=True, slots=True)
class ScenarioSet:
    schema_version: str
    track: str
    rule_profile: str
    venue: str
    instrument: str
    timeframe: str
    as_of_ms: int
    availability_mode: str
    status: SetStatus
    scenarios: tuple[WaveScenario, ...]
    unknown_reasons: tuple[str, ...] = ()
    exhaustive: bool = False
    other_or_unknown: str = "ALWAYS_POSSIBLE"
    """목록 밖 해석(다른 count·미지원 패턴·자료 문제)의 자리. 확률을 매기지 않는다."""
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------- 변환


_LABELS = {
    WavePattern.IMPULSE: ("0", "1", "2", "3", "4", "5"),
    WavePattern.ENDING_DIAGONAL: ("0", "1", "2", "3", "4", "5"),
    WavePattern.ZIGZAG: ("0", "A", "B", "C"),
}


def _ratio(a: Decimal, b: Decimal) -> float | None:
    return None if b == 0 else float(abs(a) / abs(b))


def _fib(
    key: str,
    observed: float | None,
    band: tuple[float, float] | None,
    source: str,
) -> EvidenceItem:
    if observed is None:
        return EvidenceItem("FIBONACCI", key, EvidenceState.INSUFFICIENT_ANCHORS, source=source)
    in_band = None if band is None else band[0] <= observed <= band[1]
    return EvidenceItem(
        "FIBONACCI", key, EvidenceState.OBSERVED, round(observed, 6), band, in_band, source
    )


def _evidence(pattern: WavePattern, anchors: Sequence[FourHourPivotEvidence]) -> list[EvidenceItem]:
    """앵커 가격만으로 계산할 수 있는 근거. 나머지 근거 계열은 NOT_COMPUTED 로 명시한다."""
    p = [a.price for a in anchors]
    legs = [p[i + 1] - p[i] for i in range(len(p) - 1)]

    def leg(i: int) -> Decimal | None:
        return legs[i] if i < len(legs) else None

    def ratio(i: int, j: int) -> float | None:
        a, b = leg(i), leg(j)
        return None if a is None or b is None else _ratio(a, b)

    items: list[EvidenceItem] = []
    if pattern is WavePattern.IMPULSE:
        items += [
            _fib("wave2_retrace_of_wave1", ratio(1, 0), (0.382, 0.786), "엘갤 PDF p7 · Aurra"),
            _fib("wave3_over_wave1", ratio(2, 0), None, "Aurra: 3파 1.618 흔함"),
            _fib("wave4_retrace_of_wave3", ratio(3, 2), (0.236, 0.5), "엘갤 PDF p7 · Aurra"),
            _fib("wave5_over_wave1", ratio(4, 0), None, "엘갤 PDF p7: 3파 확장 시 1:1 또는 0.618"),
        ]
        # 교대: 2파·4파의 깊이와 길이(4h 봉 수)를 원값으로만 남긴다
        if len(anchors) >= 5:
            w2_bars = (
                anchors[2].extreme_four_hour_open_ms - anchors[1].extreme_four_hour_open_ms
            ) // _H4_MS
            w4_bars = (
                anchors[4].extreme_four_hour_open_ms - anchors[3].extreme_four_hour_open_ms
            ) // _H4_MS
            items.append(
                EvidenceItem(
                    "ALTERNATION",
                    "wave4_over_wave2_duration_bars",
                    EvidenceState.OBSERVED,
                    None if w2_bars == 0 else round(w4_bars / w2_bars, 6),
                    source="엘갤 PDF p21: 2파 깊고 짧음 · 4파 얕고 김",
                )
            )
        else:
            items.append(
                EvidenceItem(
                    "ALTERNATION",
                    "wave4_over_wave2_duration_bars",
                    EvidenceState.INSUFFICIENT_ANCHORS,
                )
            )
    elif pattern is WavePattern.ZIGZAG:
        items += [
            _fib("b_retrace_of_a", ratio(1, 0), (0.382, 0.786), "엘갤 PDF p12"),
            _fib("c_over_a", ratio(2, 0), None, "엘갤 PDF p12: 1 · 1.618 · 0.618 순"),
        ]
    else:  # ENDING_DIAGONAL (수렴형: 3<1, 5<3, 4<2 는 문법이 검사한다)
        items += [
            _fib("wave3_over_wave1", ratio(2, 0), None, "엘갤 PDF p9 수렴형: 1 미만"),
            _fib("wave5_over_wave3", ratio(4, 2), None, "엘갤 PDF p9 수렴형: 1 미만"),
        ]
    for family, key in (
        ("MOMENTUM", "rsi_divergence_at_last_anchor"),
        ("VOLUME", "volume_profile_by_wave"),
        ("MULTI_TIMEFRAME", "parent_degree_consistency"),
        ("CHANNEL", "impulse_channel_fit"),
    ):
        items.append(EvidenceItem(family, key, EvidenceState.NOT_COMPUTED))
    return items


def _state(pattern: WavePattern, record: CandidateRecord, n_anchors: int) -> str:
    status = record.hypothesis_status
    labels = _LABELS[pattern]
    if status is HypothesisStatus.GRAMMAR_REJECTED:
        return "REJECTED"
    if status is HypothesisStatus.ENDING_INVALIDATED:
        return "END_INVALIDATED"
    if status is HypothesisStatus.ENDING_UNVERIFIED:
        return f"WAVE_{labels[-1]}_END_UNVERIFIED"
    return f"WAVE_{labels[min(n_anchors, len(labels) - 1)]}_FORMING"


def _invalidations(
    pattern: WavePattern,
    direction: int,
    anchors: Sequence[FourHourPivotEvidence],
    record: CandidateRecord,
) -> list[InvalidationLevel]:
    """이 시점에 계산 가능한 분석용 무효화 경계 (ELLIOTT_WAVE_ANALYSIS §6.2)."""
    below, basis = ("<=", "LOW") if direction > 0 else (">=", "HIGH")
    levels: list[InvalidationLevel] = []
    if anchors and pattern in (WavePattern.IMPULSE, WavePattern.ENDING_DIAGONAL):
        levels.append(InvalidationLevel("WAVE2_BEYOND_ORIGIN", str(anchors[0].price), below, basis))
    if pattern is WavePattern.IMPULSE and len(anchors) >= 4:
        levels.append(
            InvalidationLevel("WAVE4_OVERLAPS_WAVE1", str(anchors[1].price), below, basis)
        )
    if pattern is WavePattern.ZIGZAG and anchors:
        levels.append(InvalidationLevel("B_BEYOND_A_START", str(anchors[0].price), below, basis))
    if record.invalidation is not None:
        above, ext_basis = (">=", "HIGH") if direction > 0 else ("<=", "LOW")
        levels.append(
            InvalidationLevel(
                "END_REEXTENDED",
                str(record.invalidation.endpoint_price),
                above,
                ext_basis,
                record.invalidation.invalidated_at_ms,
            )
        )
    return levels


def _is_leg_subdivision_rule(rule: str) -> bool:
    return rule.startswith("LEG_") and "_SUBDIVISION_" in rule


def _leg_checks(record: CandidateRecord) -> list[HardCheck]:
    """다리별 하위파 판정. 문법의 조기 반환(꼭짓점 미완성)과 무관하게 증거에서 직접 계산한다."""
    scenario = record.scenario
    expected = EXPECTED_SUBDIVISIONS[scenario.pattern]
    checks: list[HardCheck] = []
    for index, proof in enumerate(scenario.subdivisions[: len(expected)], 1):
        if proof.shape is LegSubdivision.UNKNOWN:
            checks.append(HardCheck(f"LEG_{index}_SUBDIVISION", CheckStatus.UNVERIFIED))
        elif subdivision_matches(expected[index - 1], proof.shape):
            checks.append(HardCheck(f"LEG_{index}_SUBDIVISION", CheckStatus.PASS))
        elif proof.source is ProofSource.H4_DEGREE_LADDER:
            checks.append(HardCheck(f"LEG_{index}_SUBDIVISION_CONFLICT", CheckStatus.PENDING))
        else:
            checks.append(HardCheck(f"LEG_{index}_SUBDIVISION_MISMATCH", CheckStatus.FAIL))
    return checks


def _hard_checks(record: CandidateRecord) -> list[HardCheck]:
    a = record.assessment
    checks = [HardCheck(r, CheckStatus.FAIL) for r in a.failures if not _is_leg_subdivision_rule(r)]
    checks += [
        HardCheck(r, CheckStatus.PENDING) for r in a.pending if not _is_leg_subdivision_rule(r)
    ]
    if a.geometry_status is GrammarStatus.PASS:
        checks.append(HardCheck("GEOMETRY", CheckStatus.PASS))
    if record.scenario.subdivisions:
        checks += _leg_checks(record)
    else:
        checks.append(HardCheck("SUBDIVISIONS", CheckStatus.UNVERIFIED))
    return checks


def _subdivision_status(record: CandidateRecord) -> str:
    legs = _leg_checks(record)
    required = len(EXPECTED_SUBDIVISIONS[record.scenario.pattern])
    passed = sum(check.status is CheckStatus.PASS for check in legs)
    if legs and passed == required:
        return "VERIFIED"
    return "PARTIAL" if passed else "UNVERIFIED"


def _subdivision_evidence(
    record: CandidateRecord, child_degree_id: str | None
) -> list[EvidenceItem]:
    """다리별 보이는 하위 다리 수 (33 §16). 하위 degree 가 없으면 NOT_COMPUTED."""
    if not record.scenario.subdivisions:
        return [
            EvidenceItem(
                "SUBDIVISION", "child_leg_counts", EvidenceState.NOT_COMPUTED, layer="HARD_RULE"
            )
        ]
    items: list[EvidenceItem] = []
    for index, proof in enumerate(record.scenario.subdivisions, 1):
        items.append(
            EvidenceItem(
                "SUBDIVISION",
                f"leg_{index}_child_legs",
                EvidenceState.OBSERVED
                if proof.child_leg_count is not None
                else EvidenceState.INSUFFICIENT_ANCHORS,
                None if proof.child_leg_count is None else float(proof.child_leg_count),
                source=f"{child_degree_id}: {proof.shape.value}"
                + ("" if proof.reason is None else f" ({proof.reason})"),
                layer="HARD_RULE",
            )
        )
    return items


def from_candidate_snapshot(
    snapshot: CandidateSnapshot,
    *,
    venue: str,
    instrument: str,
    rule_profile: str = "EW-0",
    track: str = "EW-WA-CONTEXT",
    availability_mode: str = "BAR_CLOSE_ASSUMED",
    flags: Sequence[str] = (),
) -> ScenarioSet:
    """현재 후보 생성기(:func:`generate_bounded_candidates`) 출력을 평가자용 묶음으로 바꾼다."""
    common_flags = ("SEARCH_BOUNDED_LAST_WINDOW", *flags)
    if snapshot.reason is not None:
        return ScenarioSet(
            SCHEMA_VERSION, track, rule_profile, venue, instrument, "4h", snapshot.as_of_ms,
            availability_mode, SetStatus.INSUFFICIENT_ANCHORS, (), (snapshot.reason,),
        )  # fmt: skip
    scenarios: list[WaveScenario] = []
    for record in snapshot.records:
        pattern = record.scenario.pattern
        anchors = [
            item.candidate
            for item in record.scenario.evidence
            if item.status is EvidenceStatus.CANDIDATE and item.candidate is not None
        ]
        labels = _LABELS[pattern]
        checks = _hard_checks(record)
        evidence = _evidence(pattern, anchors) + _subdivision_evidence(
            record, snapshot.child_degree_id
        )
        evaluated = [e for e in evidence if e.layer == "GUIDELINE" and e.in_band is not None]
        scenarios.append(
            WaveScenario(
                scenario_id=record.scenario.scenario_id,
                pattern=pattern.value,
                direction=record.scenario.direction,
                degree_id=f"H4:{snapshot.degree_id}",
                parent_id=None,
                wave_count=labels,
                current_state=_state(pattern, record, len(anchors)),
                hypothesis_status=record.hypothesis_status.value,
                anchors=tuple(
                    Anchor(
                        labels[i] if i < len(labels) else f"+{i}",
                        str(a.price),
                        a.extreme_four_hour_open_ms,
                        a.confirmed_four_hour_close_ms,
                    )
                    for i, a in enumerate(anchors)
                ),
                hard_checks=tuple(checks),
                evidence=tuple(evidence),
                invalidation_levels=tuple(
                    _invalidations(pattern, record.scenario.direction, anchors, record)
                ),
                uncertainty=Uncertainty(
                    _subdivision_status(record),
                    len(snapshot.records),
                    common_flags,
                ),
                rule_support=RuleSupport(
                    hard_pass=sum(c.status is CheckStatus.PASS for c in checks),
                    hard_fail=sum(c.status is CheckStatus.FAIL for c in checks),
                    hard_pending=sum(
                        c.status in (CheckStatus.PENDING, CheckStatus.UNVERIFIED) for c in checks
                    ),
                    guideline_in_band=sum(bool(e.in_band) for e in evaluated),
                    guideline_evaluated=len(evaluated),
                ),
            )
        )
    status = (
        SetStatus.NO_VALID_COUNT
        if scenarios and all(s.current_state == "REJECTED" for s in scenarios)
        else SetStatus.SCENARIOS
    )
    return ScenarioSet(
        SCHEMA_VERSION, track, rule_profile, venue, instrument, "4h", snapshot.as_of_ms,
        availability_mode, status, tuple(scenarios),
        notes=("rule_support 는 확률이 아니다", "invalidation 은 분석 전용 — 주문·손절 아님"),
    )  # fmt: skip


# ---------------------------------------------------------------- 직렬화·검증


def to_json_dict(scenario_set: ScenarioSet) -> dict[str, Any]:
    """평가자에게 넘길 JSON 호환 dict. 키 순서는 :func:`scenario_set_digest` 에서 고정한다."""
    return json.loads(json.dumps(asdict(scenario_set), default=str))  # type: ignore[no-any-return]


def scenario_set_digest(scenario_set: ScenarioSet | Mapping[str, Any]) -> str:
    """정렬된 JSON 의 SHA-256 — 평가 결과가 어느 입력에 대한 것인지 묶는다."""
    data = to_json_dict(scenario_set) if isinstance(scenario_set, ScenarioSet) else scenario_set
    payload = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_scenario_set_dict(data: Mapping[str, Any]) -> list[str]:
    """평가자 입력 쪽 방어: 계약 위반 목록 (빈 목록이면 통과)."""
    problems: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version 이 {SCHEMA_VERSION} 이 아니다")
    if data.get("exhaustive") is not False:
        problems.append("시나리오 목록은 완전하다고 표시할 수 없다 (exhaustive=False)")
    if data.get("other_or_unknown") != "ALWAYS_POSSIBLE":
        problems.append("other_or_unknown 자리가 없다")
    ids = [s.get("scenario_id") for s in data.get("scenarios", [])]
    if len(ids) != len(set(ids)):
        problems.append("scenario_id 가 중복된다")
    for s in data.get("scenarios", []):
        support = s.get("rule_support", {})
        if support.get("probability") is not None:
            problems.append(f"{s.get('scenario_id')}: rule_support 에 확률이 들어 있다")
        if not s.get("uncertainty", {}).get("other_or_unknown_possible", False):
            problems.append(f"{s.get('scenario_id')}: 다른 해석 가능성을 닫았다")
        for level in s.get("invalidation_levels", []):
            if level.get("scope") != "ANALYSIS_ONLY":
                problems.append(f"{s.get('scenario_id')}: 무효화 경계가 분석 전용이 아니다")
    return problems
