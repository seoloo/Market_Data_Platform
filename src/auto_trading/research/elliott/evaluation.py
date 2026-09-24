"""Elliott 시나리오 평가자 인터페이스 — JEV 등 외부 평가자를 나중에 붙일 자리 (순수, I/O 없음).

JEV 의 정체·공급자·모델은 아직 정하지 않았다(OPERATIONS: 추측해 구현하지 않는다). 여기서는
**어떤 평가자든 지켜야 할 입출력 계약**만 둔다.

* 입력: :class:`~auto_trading.research.elliott.scenario_contract.ScenarioSet` 의 JSON dict.
* 출력: 시나리오마다 ``relative_support``(0~1, **상대 평가 — 확률 아님**)와 근거 문장.
  ``other_or_unknown`` 에도 값을 줄 수 있고, 판단을 거부(``ABSTAIN``)할 수 있다.
  평가자 실패·시간 초과는 ``UNAVAILABLE`` — 중립 의견으로 위장하지 않는다.
* 하나의 시나리오를 "정답"으로 확정하는 필드가 없다. 평가 결과는 Shadow 기록일 뿐
  주문·veto·사이징·보호주문에 쓰지 않는다(CURRENT_SPEC: Elliott/JEV Shadow 전용).
* 재현을 위해 평가자 ID·모델 버전·프롬프트 버전·입력 해시·원출력을 함께 보관한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from auto_trading.research.elliott.scenario_contract import scenario_set_digest

__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "AbstainEvaluator",
    "ScenarioEvaluation",
    "ScenarioEvaluator",
    "ScenarioJudgement",
    "Verdict",
    "validate_evaluation",
]

EVALUATION_SCHEMA_VERSION = "ew-scenario-eval/1"


class Verdict(StrEnum):
    RELATIVE_SUPPORT = "RELATIVE_SUPPORT"
    """시나리오들 사이의 상대 평가를 냈다."""
    ABSTAIN = "ABSTAIN"
    """판단할 근거가 부족하다고 스스로 밝혔다."""
    UNAVAILABLE = "UNAVAILABLE"
    """평가자 실패·시간 초과·형식 오류. 의견이 아니다."""


@dataclass(frozen=True, slots=True)
class ScenarioJudgement:
    scenario_id: str
    relative_support: float | None
    """0~1 상대 평가. 합이 1일 필요 없고 **확률로 해석하지 않는다.**"""
    rationale: str
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScenarioEvaluation:
    schema_version: str
    input_digest: str
    """평가한 ScenarioSet 의 :func:`scenario_set_digest` — 입력과 결과를 묶는다."""
    evaluator_id: str
    model_version: str
    prompt_version: str
    evaluated_at_ms: int
    verdict: Verdict
    judgements: tuple[ScenarioJudgement, ...]
    other_or_unknown_support: float | None
    """목록 밖 해석(다른 count·미지원 패턴·자료 문제)에 대한 상대 평가."""
    raw_output: str
    """평가자 원출력 (재현·감사용)."""
    interpretation: str = "RELATIVE_NOT_PROBABILITY"


class ScenarioEvaluator(Protocol):
    """평가자 공통 인터페이스. 구현은 공급자 비종속으로 이 뒤에 둔다."""

    evaluator_id: str

    def evaluate(
        self, scenario_set: Mapping[str, Any], *, evaluated_at_ms: int
    ) -> ScenarioEvaluation: ...


@dataclass(frozen=True, slots=True)
class AbstainEvaluator:
    """자리표시 평가자 — 항상 판단을 거부한다. 파이프라인·저장 형식 시험용."""

    evaluator_id: str = "abstain"

    def evaluate(
        self, scenario_set: Mapping[str, Any], *, evaluated_at_ms: int
    ) -> ScenarioEvaluation:
        return ScenarioEvaluation(
            schema_version=EVALUATION_SCHEMA_VERSION,
            input_digest=scenario_set_digest(scenario_set),
            evaluator_id=self.evaluator_id,
            model_version="none",
            prompt_version="none",
            evaluated_at_ms=evaluated_at_ms,
            verdict=Verdict.ABSTAIN,
            judgements=(),
            other_or_unknown_support=None,
            raw_output="",
        )


def validate_evaluation(
    evaluation: ScenarioEvaluation, scenario_set: Mapping[str, Any]
) -> list[str]:
    """평가 결과가 계약을 지키는지. 빈 목록이면 통과 — 통과해도 Shadow 기록일 뿐이다."""
    problems: list[str] = []
    if evaluation.schema_version != EVALUATION_SCHEMA_VERSION:
        problems.append(f"schema_version 이 {EVALUATION_SCHEMA_VERSION} 이 아니다")
    if evaluation.interpretation != "RELATIVE_NOT_PROBABILITY":
        problems.append("평가를 확률로 표시할 수 없다")
    if evaluation.input_digest != scenario_set_digest(scenario_set):
        problems.append("input_digest 가 입력 시나리오 묶음과 다르다")
    known = {s["scenario_id"] for s in scenario_set.get("scenarios", [])}
    seen: set[str] = set()
    for j in evaluation.judgements:
        if j.scenario_id not in known:
            problems.append(f"입력에 없는 시나리오를 평가했다: {j.scenario_id}")
        if j.scenario_id in seen:
            problems.append(f"같은 시나리오를 두 번 평가했다: {j.scenario_id}")
        seen.add(j.scenario_id)
        if j.relative_support is not None and not 0.0 <= j.relative_support <= 1.0:
            problems.append(f"{j.scenario_id}: relative_support 가 0~1 밖이다")
    other = evaluation.other_or_unknown_support
    if other is not None and not 0.0 <= other <= 1.0:
        problems.append("other_or_unknown_support 가 0~1 밖이다")
    if evaluation.verdict is not Verdict.RELATIVE_SUPPORT and evaluation.judgements:
        problems.append(f"{evaluation.verdict} 인데 시나리오 평가가 들어 있다")
    if evaluation.verdict is Verdict.RELATIVE_SUPPORT and not evaluation.judgements:
        problems.append("RELATIVE_SUPPORT 인데 평가가 비어 있다")
    return problems
