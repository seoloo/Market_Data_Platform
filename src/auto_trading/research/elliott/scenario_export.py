"""EW-3d′ 시나리오 묶음을 평가자(JEV 등) 입력 형식으로 내보낸다 (문서 39 이후, 순수 함수).

형식은 기존 ``ew-scenario-set/1``(:mod:`scenario_contract`) 그대로다. JEV 형태는 정하지 않는다.

* 순위 정보는 평가자 입력에서 뺀다 — 시나리오는 기준 순위가 아니라 ``scenario_id`` 순으로 나열하고,
  기준 순위 규칙의 순위는 **별도 사이드카**로만 돌려준다(섀도 비교용, 평가자에게 주지 않는다).
* 확률을 적지 않는다. 목록 밖 가능성(other_or_unknown)과 목록 밖 후보 수를 항상 적는다.
* 무효화 가격은 분석 전용이다. 주문·손절·veto 에 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.grammar import WavePattern
from auto_trading.research.elliott.h4_evidence import FourHourPivotEvidence
from auto_trading.research.elliott.parser import LEG_NEEDS, FinePivot, Label, Parse
from auto_trading.research.elliott.ranking import guideline_distance
from auto_trading.research.elliott.ranking_v2 import guideline_distance_v2
from auto_trading.research.elliott.scanner import ScenarioList, Trigger
from auto_trading.research.elliott.scenario_contract import (
    SCHEMA_VERSION,
    Anchor,
    CheckStatus,
    EvidenceItem,
    EvidenceState,
    HardCheck,
    InvalidationLevel,
    RuleSupport,
    ScenarioSet,
    SetStatus,
    Uncertainty,
    WaveScenario,
    _evidence,
)

__all__ = ["FIB_LEVELS", "RULE_PROFILE", "export_scenario_set", "flat_classes"]

RULE_PROFILE = "EW-3d'"
"""기본값(재현용). EW-3e 로 만든 묶음은 ``rule_profile="EW-3e"`` 를 넘긴다 (문서 58)."""
FIB_LEVELS = (Decimal("0.382"), Decimal("0.5"), Decimal("0.618"))
_LABELS = {
    Label.IMPULSE: ("0", "1", "2", "3", "4", "5"),
    Label.ZIGZAG: ("0", "A", "B", "C"),
    Label.FLAT: ("0", "A", "B", "C"),
    Label.TRIANGLE: ("0", "A", "B", "C", "D", "E"),
}
_EW4 = (Label.FLAT, Label.TRIANGLE)
_FOURLEAF = "FourLeaf(TradingView) 2026-02-27 · NEoWave — 분류 라벨일 뿐"


def _scenario_id(parse: Parse, pivots: Sequence[FinePivot], as_of_ms: int) -> str:
    opens = "-".join(str(pivots[k].hour_open_ms) for k in parse.bounds)
    return f"EW3d:{parse.pattern.value}:{opens}:{as_of_ms}"


def _fib_levels(key: str, start: FinePivot, end: FinePivot) -> list[EvidenceItem]:
    """start→end 움직임의 되돌림 가격(분석 전용 참고값)."""
    move = end.price - start.price
    return [
        EvidenceItem(
            "FIB_LEVEL",
            f"{key}_retrace_{level}",
            EvidenceState.OBSERVED,
            float(end.price - move * level),
            source="피보나치 되돌림 가격 (end − move × 비율)",
        )
        for level in FIB_LEVELS
    ]


def flat_classes(b_over_a: float, c_over_b: float) -> tuple[str, str]:
    """문서 60 §4 플랫 하위 유형 (B 분류, C 분류). 판정·순위에 쓰지 않는 근거 라벨."""
    if b_over_a < 0.81:
        b = "B_FAILURE"
    elif b_over_a <= 1.0:
        b = "NORMAL"
    elif b_over_a <= 1.382:
        b = "IRREGULAR"
    else:
        b = "RUNNING"
    c = "C_FAILURE" if c_over_b < 1.0 else "NORMAL" if c_over_b <= 1.382 else "ELONGATED"
    return b, c


def _ew4_evidence(
    pattern: Label, bounds: Sequence[FinePivot], direction: int
) -> list[EvidenceItem]:
    """플랫·삼각형 근거 (문서 60 §4) — 관찰값과 분류 라벨. 확률·신호가 아니다."""
    p = [b.price for b in bounds]
    legs = [abs(p[k + 1] - p[k]) for k in range(len(p) - 1)]

    def ratio(num: int, den: int) -> float:
        return round(float(legs[num] / legs[den]), 6)

    obs = EvidenceState.OBSERVED
    items: list[EvidenceItem] = []
    if pattern is Label.FLAT:
        ba, ca, cb = ratio(1, 0), ratio(2, 0), ratio(2, 1)
        b_class, c_class = flat_classes(ba, cb)
        items += [
            EvidenceItem(
                "FIBONACCI", "b_retrace_of_a", obs, ba, source="문서 60 F1·F2: 0.618~1.618"
            ),
            EvidenceItem("FIBONACCI", "c_over_a", obs, ca, source="문서 60 F3: 0.382~2.618"),
            EvidenceItem("FIBONACCI", "c_over_b", obs, cb, source=_FOURLEAF),
            EvidenceItem(
                "PATTERN_CLASS",
                f"flat_b={b_class}",
                obs,
                ba,
                source="B/A 0.81·1.0·1.382 — " + _FOURLEAF,
            ),
            EvidenceItem(
                "PATTERN_CLASS", f"flat_c={c_class}", obs, cb, source="C/B 1.0·1.382 — " + _FOURLEAF
            ),
        ]
        if (b_class, c_class) == ("B_FAILURE", "C_FAILURE"):
            items.append(
                EvidenceItem(
                    "HYPOTHESIS",
                    "double_failure_full_retrace_to_a_start",
                    obs,
                    float(p[0]),
                    source="이중 미달형 뒤 플랫 시작까지 100% 되돌림 — 미검증 가설 · " + _FOURLEAF,
                    layer="SUBJECTIVE",
                )
            )
    else:
        items += [
            EvidenceItem(
                "FIBONACCI",
                f"{b}_over_{a}",
                obs,
                ratio(k + 1, k),
                source="교과서 대표값 0.618 (순위 v2 목표) — 범위 규칙 아님",
            )
            for k, (a, b) in enumerate(zip("abcd", "bcde", strict=True))
        ]
        items.append(
            EvidenceItem(
                "PROJECTION",
                "triangle_thrust_target",
                obs,
                float(p[-1] - direction * legs[0]),
                source="E 끝에서 A 반대 방향으로 가장 넓은 폭(A) — FourLeaf 2026-02-19 · 교과서",
            )
        )
    for family, key in (
        ("MOMENTUM", "rsi_divergence_at_last_anchor"),
        ("VOLUME", "volume_profile_by_wave"),
        ("MULTI_TIMEFRAME", "parent_degree_consistency"),
        ("CHANNEL", "triangle_boundary_fit" if pattern is Label.TRIANGLE else "channel_fit"),
    ):
        items.append(EvidenceItem(family, key, EvidenceState.NOT_COMPUTED))
    return items


def _one(parse: Parse, pivots: Sequence[FinePivot], as_of_ms: int, total: int,
         flags: tuple[str, ...]) -> WaveScenario:  # fmt: skip
    bounds = [pivots[k] for k in parse.bounds]
    labels = _LABELS[parse.pattern]
    direction = 1 if bounds[0].kind is SwingKind.LOW else -1
    anchors_4h = [
        FourHourPivotEvidence(p.kind, p.price, p.hour_open_ms, p.available_at_ms) for p in bounds
    ]
    ew4 = parse.pattern in _EW4
    need = LEG_NEEDS[parse.pattern]
    verified = [lab is want for lab, want in zip(parse.leg_labels, need, strict=True)]
    checks = [HardCheck("TOP_LEVEL_HARD_RULES", CheckStatus.PASS)] + [
        HardCheck(f"LEG_{i}_SUBDIVISION", CheckStatus.PASS if ok else CheckStatus.UNVERIFIED)
        for i, ok in enumerate(verified, 1)
    ]
    if ew4:
        pattern_name = parse.pattern.value
        evidence = _ew4_evidence(parse.pattern, bounds, direction)
    else:
        wave = WavePattern.IMPULSE if parse.pattern is Label.IMPULSE else WavePattern.ZIGZAG
        pattern_name = wave.value
        evidence = _evidence(wave, anchors_4h)
    evidence += [
        EvidenceItem(
            "SUBDIVISION",
            f"leg_{i}_reading",
            EvidenceState.OBSERVED,
            source=lab.value,
            layer="HARD_RULE",
        )
        for i, lab in enumerate(parse.leg_labels, 1)
    ]
    evidence += _fib_levels("whole", bounds[0], bounds[-1])
    evidence += _fib_levels("last_leg", bounds[-2], bounds[-1])
    evidence.append(
        EvidenceItem(
            "GUIDELINE_DISTANCE",
            "mean_abs_log_ratio_to_textbook",
            EvidenceState.OBSERVED,
            round((guideline_distance_v2 if ew4 else guideline_distance)(parse, pivots), 6),
            source="문서 60 §3.3 순위 v2 목표비" if ew4 else "교과서 목표비 (엘갤 PDF p7·p12)",
        )
    )
    below, basis = ("<=", "LOW") if direction > 0 else (">=", "HIGH")
    above, ext_basis = (">=", "HIGH") if direction > 0 else ("<=", "LOW")
    levels: list[InvalidationLevel] = []
    if parse.pattern is Label.FLAT:
        # 확장 플랫은 B 가 A 시작을 넘어도 된다 — 대신 C 가 A 의 2.618 배를 넘으면 플랫이 아니다(F3)
        a_move = bounds[1].price - bounds[0].price
        c_limit = bounds[2].price + a_move * Decimal("2.618")
        levels.append(InvalidationLevel("C_BEYOND_2618_OF_A", str(c_limit), above, ext_basis))
    elif parse.pattern is Label.TRIANGLE:
        levels.append(InvalidationLevel("E_BEYOND_C_END", str(bounds[3].price), above, ext_basis))
    else:
        levels.append(
            InvalidationLevel(
                "WAVE2_BEYOND_ORIGIN" if parse.pattern is Label.IMPULSE else "B_BEYOND_A_START",
                str(bounds[0].price),
                below,
                basis,
            )
        )
    if parse.pattern is Label.IMPULSE:
        levels.append(InvalidationLevel("WAVE4_OVERLAPS_WAVE1", str(bounds[1].price), below, basis))
    levels.append(InvalidationLevel("END_REEXTENDED", str(bounds[-1].price), above, ext_basis))
    evaluated = [e for e in evidence if e.layer == "GUIDELINE" and e.in_band is not None]
    status = "VERIFIED" if all(verified) else "PARTIAL" if any(verified) else "UNVERIFIED"
    return WaveScenario(
        scenario_id=_scenario_id(parse, pivots, as_of_ms),
        pattern=pattern_name,
        direction=direction,
        degree_id="4h-count/1h-subwave",
        parent_id=None,
        wave_count=labels,
        current_state=f"WAVE_{labels[-1]}_END_CANDIDATE",
        hypothesis_status="END_CANDIDATE",
        anchors=tuple(
            Anchor(labels[i], str(p.price), p.hour_open_ms, p.available_at_ms)
            for i, p in enumerate(bounds)
        ),
        hard_checks=tuple(checks),
        evidence=tuple(evidence),
        invalidation_levels=tuple(levels),
        uncertainty=Uncertainty(status, total, flags),
        rule_support=RuleSupport(
            hard_pass=1 + sum(verified),
            hard_fail=0,
            hard_pending=len(verified) - sum(verified),
            guideline_in_band=sum(bool(e.in_band) for e in evaluated),
            guideline_evaluated=len(evaluated),
        ),
    )


def export_scenario_set(
    trigger: Trigger,
    scenarios: ScenarioList,
    *,
    venue: str,
    instrument: str,
    flags: Sequence[str] = (),
    rule_profile: str = RULE_PROFILE,
) -> tuple[ScenarioSet, dict[str, int]]:
    """(평가자 입력 묶음, 사이드카 {scenario_id: 기준 순위 규칙의 비관적 순위}).

    사이드카는 평가자에게 넘기지 않는다 — 섀도 비교(문서 39 §6)에서만 쓴다.
    """
    pivots = trigger.segment
    common = (
        "SEARCH_BOUNDED_60D",
        f"SET_SIZE_{scenarios.size}_OF_{scenarios.total}",
        *flags,
    )
    items = [
        (_one(parse, pivots, trigger.close_ms, scenarios.total, common), parse)
        for parse, _ in scenarios.top
    ]
    items.sort(key=lambda item: item[0].scenario_id)
    sidecar = {
        scenario.scenario_id: int(scenarios.pessimistic_rank(parse) or 0)
        for scenario, parse in items
    }
    status = SetStatus.SCENARIOS if items else SetStatus.INSUFFICIENT_ANCHORS
    ew4_note = (
        (
            "플랫·삼각형 후보 빈도는 무작위 대조와 같거나 낮다(문서 60 §8)"
            " — 목록에 있다는 것이 그 모양의 근거가 아니다",
        )
        if any(parse.pattern in _EW4 for _, parse in items)
        else ()
    )
    result = ScenarioSet(
        schema_version=SCHEMA_VERSION,
        track="EW-WA-CONTEXT",
        rule_profile=rule_profile,
        venue=venue,
        instrument=instrument,
        timeframe=f"{Timeframe.H4.value}+{Timeframe.H1.value}",
        as_of_ms=trigger.close_ms,
        availability_mode="BAR_CLOSE_ASSUMED",
        status=status,
        scenarios=tuple(s for s, _ in items),
        unknown_reasons=() if items else ("NO_CANDIDATE",),
        notes=(
            "rule_support 는 확률이 아니다",
            "시나리오 순서는 scenario_id 순 — 순위 정보가 아니다",
            f"목록 밖 후보 {scenarios.outside} 개 + 문법 밖 해석(other_or_unknown)",
            "invalidation·FIB_LEVEL 은 분석 전용 — 주문·손절 아님",
            *ew4_note,
        ),
    )
    return result, sidecar
