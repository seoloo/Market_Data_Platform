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
from auto_trading.research.elliott.parser import FinePivot, Label, Parse
from auto_trading.research.elliott.ranking import guideline_distance
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

__all__ = ["FIB_LEVELS", "RULE_PROFILE", "export_scenario_set"]

RULE_PROFILE = "EW-3d'"
FIB_LEVELS = (Decimal("0.382"), Decimal("0.5"), Decimal("0.618"))
_LABELS = {
    Label.IMPULSE: ("0", "1", "2", "3", "4", "5"),
    Label.ZIGZAG: ("0", "A", "B", "C"),
}
_NEED = (Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE)


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


def _one(parse: Parse, pivots: Sequence[FinePivot], as_of_ms: int, total: int,
         flags: tuple[str, ...]) -> WaveScenario:  # fmt: skip
    bounds = [pivots[k] for k in parse.bounds]
    labels = _LABELS[parse.pattern]
    direction = 1 if bounds[0].kind is SwingKind.LOW else -1
    anchors_4h = [
        FourHourPivotEvidence(p.kind, p.price, p.hour_open_ms, p.available_at_ms) for p in bounds
    ]
    wave = WavePattern.IMPULSE if parse.pattern is Label.IMPULSE else WavePattern.ZIGZAG
    need = _NEED[: len(parse.leg_labels)]
    verified = [lab is want for lab, want in zip(parse.leg_labels, need, strict=True)]
    checks = [HardCheck("TOP_LEVEL_HARD_RULES", CheckStatus.PASS)] + [
        HardCheck(f"LEG_{i}_SUBDIVISION", CheckStatus.PASS if ok else CheckStatus.UNVERIFIED)
        for i, ok in enumerate(verified, 1)
    ]
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
            round(guideline_distance(parse, pivots), 6),
            source="교과서 목표비 (엘갤 PDF p7·p12)",
        )
    )
    below, basis = ("<=", "LOW") if direction > 0 else (">=", "HIGH")
    above, ext_basis = (">=", "HIGH") if direction > 0 else ("<=", "LOW")
    levels = [
        InvalidationLevel(
            "WAVE2_BEYOND_ORIGIN" if parse.pattern is Label.IMPULSE else "B_BEYOND_A_START",
            str(bounds[0].price),
            below,
            basis,
        )
    ]
    if parse.pattern is Label.IMPULSE:
        levels.append(InvalidationLevel("WAVE4_OVERLAPS_WAVE1", str(bounds[1].price), below, basis))
    levels.append(InvalidationLevel("END_REEXTENDED", str(bounds[-1].price), above, ext_basis))
    evaluated = [e for e in evidence if e.layer == "GUIDELINE" and e.in_band is not None]
    status = "VERIFIED" if all(verified) else "PARTIAL" if any(verified) else "UNVERIFIED"
    return WaveScenario(
        scenario_id=_scenario_id(parse, pivots, as_of_ms),
        pattern=wave.value,
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
    result = ScenarioSet(
        schema_version=SCHEMA_VERSION,
        track="EW-WA-CONTEXT",
        rule_profile=RULE_PROFILE,
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
        ),
    )
    return result, sidecar
