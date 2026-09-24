"""완성 4h 이상 후보 증거의 Elliott 엄격 문법만 검사한다.

이 모듈은 후보를 탐색하거나 시장에 파동 라벨을 붙이지 않는다. `PASS`는 제공된
하위파 증거를 전제로 한 문법 통과이며 정답 확률·거래 신호가 아니다.
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
from auto_trading.research.elliott.h4_evidence import (
    EvidenceStatus,
    FourHourEvidence,
    FourHourPivotEvidence,
)
from auto_trading.research.elliott.pivots import closed_ew0_candles


class WavePattern(StrEnum):
    IMPULSE = "IMPULSE"
    ZIGZAG = "ZIGZAG"
    ENDING_DIAGONAL = "ENDING_DIAGONAL"


class GrammarStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class LegSubdivision(StrEnum):
    MOTIVE_FIVE = "MOTIVE_FIVE"
    IMPULSE_FIVE = "IMPULSE_FIVE"
    CORRECTIVE_THREE = "CORRECTIVE_THREE"
    UNKNOWN = "UNKNOWN"


class ProofSource(StrEnum):
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    H4_DEGREE_LADDER = "H4_DEGREE_LADDER"
    """바로 아래 degree 의 4h 후보 증거로 읽은 하위 구조 (33 §16). 해상도 한계 때문에
    불일치는 FAIL 이 아니라 보류(CONFLICT)이고, 읽지 못하면 UNKNOWN 모양을 허용한다."""


@dataclass(frozen=True, slots=True)
class LegSubdivisionProof:
    """하위파 표식의 구간·가용 시각. 합성 fixture 또는 4h degree 사다리 출처만 인증한다."""

    shape: LegSubdivision
    start_four_hour_open_ms: int
    end_four_hour_open_ms: int
    available_at_ms: int
    timeframe: Timeframe
    source: ProofSource
    source_id: str
    child_leg_count: int | None = None
    """사다리 출처: 바로 아래 degree 에서 보이는 하위 다리 수 (정렬 실패면 None)."""
    reason: str | None = None
    """사다리 출처에서 모양이 UNKNOWN 인 이유 (33 §16)."""


class ParentRole(StrEnum):
    WAVE5 = "WAVE5"
    C = "C"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class GrammarScenario:
    scenario_id: str
    pattern: WavePattern
    direction: int  # +1 상승, -1 하락
    evidence: tuple[FourHourEvidence, ...]
    subdivisions: tuple[LegSubdivisionProof, ...] = ()
    parent_role: ParentRole = ParentRole.UNKNOWN


@dataclass(frozen=True, slots=True)
class GrammarAssessment:
    scenario_id: str
    pattern: WavePattern
    status: GrammarStatus
    geometry_status: GrammarStatus
    failures: tuple[str, ...]
    pending: tuple[str, ...]
    as_of_ms: int


def validate_scenarios(
    four_hour_candles: Sequence[Candle],
    scenarios: Sequence[GrammarScenario],
    as_of_ms: int,
) -> tuple[GrammarAssessment, ...]:
    """서로 경쟁하는 입력 후보를 정렬·선택·확률화하지 않고 모두 검사한다."""
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)) or any(not scenario_id for scenario_id in ids):
        raise DataIntegrityError("scenario_id는 비어 있지 않고 중복되지 않아야 한다")
    return tuple(validate_scenario(four_hour_candles, scenario, as_of_ms) for scenario in scenarios)


def validate_scenario(
    four_hour_candles: Sequence[Candle],
    scenario: GrammarScenario,
    as_of_ms: int,
) -> GrammarAssessment:
    """마감 4h 후보의 연속 구간만 읽는 순수 문법 검사."""
    if not isinstance(scenario.pattern, WavePattern):
        raise DataIntegrityError("지원하지 않는 Elliott 문법")
    if type(scenario.direction) is not int or scenario.direction not in (-1, 1):
        raise DataIntegrityError("Elliott 방향은 +1 또는 -1이어야 한다")
    if scenario.pattern is WavePattern.ENDING_DIAGONAL and not isinstance(
        scenario.parent_role, ParentRole
    ):
        raise DataIntegrityError("ending diagonal 부모 위치 표식이 유효하지 않다")
    closed = closed_ew0_candles(four_hour_candles, as_of_ms)
    if not closed or closed[-1].close_ms != as_of_ms:
        raise DataIntegrityError("마지막 완성 4h 마감에서만 문법을 평가한다")
    bars_by_close = {bar.close_ms: bar for bar in closed}
    bars_by_open = {bar.open_ms: bar for bar in closed}
    if not scenario.evidence or scenario.evidence[-1].close_ms != as_of_ms:
        raise DataIntegrityError("증거 구간은 현재 4h 마감까지 이어져야 한다")

    anchors: list[FourHourPivotEvidence] = []
    has_unknown = False
    prior_close: int | None = None
    for item in scenario.evidence:
        if item.close_ms not in bars_by_close or (
            prior_close is not None and item.close_ms != prior_close + Timeframe.H4.ms
        ):
            raise DataIntegrityError("증거 구간이 4h 격자에서 누락·중복·역순이다")
        prior_close = item.close_ms
        if item.status is EvidenceStatus.UNKNOWN:
            has_unknown = True
            continue
        if item.status is EvidenceStatus.NO_EVENT:
            if item.candidate is not None:
                raise DataIntegrityError("NO_EVENT에 후보가 들어 있다")
            continue
        candidate = item.candidate
        if candidate is None or candidate.confirmed_four_hour_close_ms != item.close_ms:
            raise DataIntegrityError("후보 확정 시각이 증거 4h와 다르다")
        extreme_bar = bars_by_open.get(candidate.extreme_four_hour_open_ms)
        if (
            extreme_bar is None
            or candidate.extreme_four_hour_open_ms >= bars_by_close[item.close_ms].open_ms
        ):
            raise DataIntegrityError("후보 극값은 이전 완성 4h에 있어야 한다")
        expected_price = extreme_bar.high if candidate.kind is SwingKind.HIGH else extreme_bar.low
        if candidate.price != expected_price:
            raise DataIntegrityError("후보 가격이 극값 4h의 high/low와 다르다")
        anchors.append(candidate)

    if has_unknown:
        return _assessment(
            scenario,
            GrammarStatus.UNKNOWN,
            GrammarStatus.UNKNOWN,
            (),
            ("UNKNOWN_EVIDENCE_IN_WINDOW",),
            as_of_ms,
        )
    if scenario.subdivisions:
        if len(scenario.subdivisions) != len(anchors) - 1:
            raise DataIntegrityError("하위파 증거 수가 후보 다리 수와 다르다")
        for index, proof in enumerate(scenario.subdivisions):
            if (
                not isinstance(proof, LegSubdivisionProof)
                or not isinstance(proof.shape, LegSubdivision)
                or not isinstance(proof.source, ProofSource)
                or (
                    proof.shape is LegSubdivision.UNKNOWN
                    and (proof.source is not ProofSource.H4_DEGREE_LADDER or not proof.reason)
                )
                or proof.timeframe is not Timeframe.H4
                or not proof.source_id
                or proof.start_four_hour_open_ms != anchors[index].extreme_four_hour_open_ms
                or proof.end_four_hour_open_ms != anchors[index + 1].extreme_four_hour_open_ms
                or not anchors[index + 1].confirmed_four_hour_close_ms
                <= proof.available_at_ms
                <= as_of_ms
            ):
                raise DataIntegrityError("하위파 증거의 출처·4h 구간·가용 시각이 유효하지 않다")
    required = 4 if scenario.pattern is WavePattern.ZIGZAG else 6
    expected_subdivisions = EXPECTED_SUBDIVISIONS[scenario.pattern]
    if len(anchors) < required:
        return _assessment(
            scenario,
            GrammarStatus.PENDING,
            GrammarStatus.PENDING,
            (),
            ("INCOMPLETE_ANCHORS",),
            as_of_ms,
        )
    if len(anchors) > required:
        return _assessment(
            scenario,
            GrammarStatus.FAIL,
            GrammarStatus.FAIL,
            ("EXTRA_ANCHORS_IN_WINDOW",),
            (),
            as_of_ms,
        )
    failures = _anchor_failures(anchors, scenario.direction)
    pending: list[str] = []
    if scenario.pattern is WavePattern.IMPULSE:
        shape_failures, shape_pending = impulse_rules(anchors, closed, scenario.direction)
    elif scenario.pattern is WavePattern.ZIGZAG:
        shape_failures, shape_pending = _zigzag(anchors, scenario.direction)
    else:
        shape_failures, shape_pending = ending_diagonal_rules(anchors, scenario.direction)
        if scenario.parent_role is ParentRole.OTHER:
            shape_failures.append("ENDING_PARENT_NOT_5_OR_C")
        elif scenario.parent_role is ParentRole.UNKNOWN:
            shape_pending.append("ENDING_PARENT_UNVERIFIED")
    failures.extend(shape_failures)
    pending.extend(shape_pending)
    geometry = (
        GrammarStatus.FAIL if failures else GrammarStatus.PENDING if pending else GrammarStatus.PASS
    )

    supplied: tuple[LegSubdivisionProof | None, ...] = scenario.subdivisions or (None,) * (
        required - 1
    )
    for index, (supplied_proof, expected_shape) in enumerate(
        zip(supplied, expected_subdivisions, strict=True), 1
    ):
        actual = LegSubdivision.UNKNOWN if supplied_proof is None else supplied_proof.shape
        if actual is LegSubdivision.UNKNOWN:
            pending.append(f"LEG_{index}_SUBDIVISION_UNVERIFIED")
        elif not subdivision_matches(expected_shape, actual):
            assert supplied_proof is not None
            if supplied_proof.source is ProofSource.H4_DEGREE_LADDER:
                pending.append(f"LEG_{index}_SUBDIVISION_CONFLICT")
            else:
                failures.append(f"LEG_{index}_SUBDIVISION_MISMATCH")
    status = (
        GrammarStatus.FAIL if failures else GrammarStatus.PENDING if pending else GrammarStatus.PASS
    )
    return _assessment(scenario, status, geometry, tuple(failures), tuple(pending), as_of_ms)


def _assessment(
    scenario: GrammarScenario,
    status: GrammarStatus,
    geometry_status: GrammarStatus,
    failures: tuple[str, ...],
    pending: tuple[str, ...],
    as_of_ms: int,
) -> GrammarAssessment:
    return GrammarAssessment(
        scenario_id=scenario.scenario_id,
        pattern=scenario.pattern,
        status=status,
        geometry_status=geometry_status,
        failures=failures,
        pending=pending,
        as_of_ms=as_of_ms,
    )


EXPECTED_SUBDIVISIONS: dict[WavePattern, tuple[LegSubdivision, ...]] = {
    WavePattern.IMPULSE: (
        LegSubdivision.MOTIVE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.IMPULSE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.MOTIVE_FIVE,
    ),
    WavePattern.ZIGZAG: (
        LegSubdivision.MOTIVE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.MOTIVE_FIVE,
    ),
    WavePattern.ENDING_DIAGONAL: (LegSubdivision.CORRECTIVE_THREE,) * 5,
}
"""패턴별 각 다리에 요구되는 하위파 모양."""


def subdivision_matches(expected: LegSubdivision, actual: LegSubdivision) -> bool:
    """motive 자리에는 impulse 도 허용한다. UNKNOWN 은 일치가 아니다."""
    if actual is LegSubdivision.UNKNOWN:
        return False
    return actual is expected or (
        expected is LegSubdivision.MOTIVE_FIVE and actual is LegSubdivision.IMPULSE_FIVE
    )


def anchor_failures(anchors: list[FourHourPivotEvidence], direction: int) -> list[str]:
    """꼭짓점 시작 종류·시간 순서·고저 교대 검사 (하위 degree 읽기에서도 재사용)."""
    return _anchor_failures(anchors, direction)


def _anchor_failures(anchors: list[FourHourPivotEvidence], direction: int) -> list[str]:
    first_kind = SwingKind.LOW if direction > 0 else SwingKind.HIGH
    failures: list[str] = []
    if anchors[0].kind is not first_kind:
        failures.append("ANCHOR_START_KIND")
    for previous, current in pairwise(anchors):
        if (
            current.extreme_four_hour_open_ms <= previous.extreme_four_hour_open_ms
            or current.confirmed_four_hour_close_ms <= previous.confirmed_four_hour_close_ms
            or current.kind is previous.kind
        ):
            failures.append("ANCHOR_ORDER_OR_ALTERNATION")
            break
    return failures


def impulse_rules(
    anchors: list[FourHourPivotEvidence], bars: Sequence[Candle], direction: int
) -> tuple[list[str], list[str]]:
    """여섯 꼭짓점 impulse 엄격 규칙 (실패, 보류). 하위 degree 읽기에서도 같은 규칙을 쓴다."""
    q = [direction * anchor.price for anchor in anchors]
    lengths = [abs(q[index] - q[index - 1]) for index in range(1, 6)]
    failures: list[str] = []
    pending: list[str] = []
    if not q[1] > q[0]:
        failures.append("IMPULSE_W1_DIRECTION")
    if not q[0] < q[2] < q[1]:
        failures.append("IMPULSE_W2_RETRACE")
    if not q[3] > q[1]:
        failures.append("IMPULSE_W3_EXTENSION")
    if not q[1] < q[4] < q[3]:
        failures.append("IMPULSE_W4_ENDPOINT_NONOVERLAP")
    if not q[5] > q[4]:
        failures.append("IMPULSE_W5_DIRECTION")
    if lengths[2] < min(lengths[0], lengths[4]):
        failures.append("IMPULSE_W3_SHORTEST")

    p3_open = anchors[3].extreme_four_hour_open_ms
    p4_open = anchors[4].extreme_four_hour_open_ms
    wave4_bars = tuple(bar for bar in bars if p3_open < bar.open_ms <= p4_open)
    if not wave4_bars:
        failures.append("IMPULSE_W4_EMPTY_INTERVAL")
    elif any(_directed_low(bar, direction) <= q[1] for bar in wave4_bars):
        failures.append("IMPULSE_W4_WICK_OVERLAP")
    p3_bar = next(bar for bar in bars if bar.open_ms == p3_open)
    if _directed_low(p3_bar, direction) <= q[1]:
        pending.append("IMPULSE_W4_START_BAR_ORDER_UNKNOWN")
    if q[5] <= q[3]:
        pending.append("IMPULSE_TRUNCATION_UNVERIFIED")
    return failures, pending


def _zigzag(anchors: list[FourHourPivotEvidence], direction: int) -> tuple[list[str], list[str]]:
    q = [direction * anchor.price for anchor in anchors]
    failures: list[str] = []
    if not q[1] > q[0]:
        failures.append("ZIGZAG_A_DIRECTION")
    if not q[0] < q[2] < q[1]:
        failures.append("ZIGZAG_B_STRICT_RETRACE")
    if not q[3] > q[1]:
        failures.append("ZIGZAG_NORMAL_C_EXTENSION")
    return failures, []


def ending_diagonal_rules(
    anchors: list[FourHourPivotEvidence], direction: int
) -> tuple[list[str], list[str]]:
    """여섯 꼭짓점 수렴 대각선 기하 (실패, 보류)."""
    q = [direction * anchor.price for anchor in anchors]
    lengths = [abs(q[index] - q[index - 1]) for index in range(1, 6)]
    failures: list[str] = []
    if not (q[1] > q[0] and q[0] < q[2] and q[2] < q[4] < q[1] and q[3] > q[1] and q[5] > q[3]):
        failures.append("ENDING_ENDPOINT_GEOMETRY")
    if not (lengths[0] > lengths[2] > lengths[4] and lengths[1] > lengths[3]):
        failures.append("ENDING_LEG_CONTRACTION")
    times = [anchor.extreme_four_hour_open_ms // Timeframe.H4.ms for anchor in anchors]
    upper_at_4 = _line(q[1], q[3], times[1], times[3], times[4])
    lower_at_4 = _line(q[2], q[4], times[2], times[4], times[4])
    upper_at_5 = _line(q[1], q[3], times[1], times[3], times[5])
    lower_at_5 = _line(q[2], q[4], times[2], times[4], times[5])
    width4 = upper_at_4 - lower_at_4
    width5 = upper_at_5 - lower_at_5
    if not (width4 > width5 > 0):
        failures.append("ENDING_TRENDLINE_NOT_CONTRACTING")
    return failures, []


def _directed_low(bar: Candle, direction: int) -> Decimal:
    return bar.low if direction > 0 else -bar.high


def _line(v0: Decimal, v1: Decimal, t0: int, t1: int, t: int) -> Decimal:
    if t1 <= t0:
        raise DataIntegrityError("추세선 기준점의 4h 시간이 증가하지 않는다")
    return v0 + (v1 - v0) * Decimal(t - t0) / Decimal(t1 - t0)
