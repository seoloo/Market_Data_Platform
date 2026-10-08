"""JEV × Elliott 2 차 시도 — 근거를 문장으로, 두 후보씩 비교 (문서 173 §2, 순수, I/O 없음).

172 와 다른 점 (같은 입력의 문구 · 형식만 바꾼 재시도가 아니다):

* **입력 형태**: 꼭짓점 숫자 목록을 주지 않는다. 코드가 계산한 근거(파동별 가격 변화 · 시간,
  교과서 비율과 범위 안/밖, 다리별 하위 구조와 그 다리가 가져야 할 역할)를 **영어 문장**으로 준다.
* **질문 형태**: "이 카운트가 맞나" 대신 "두 카운트 중 어느 쪽이 더 교과서적인가" (Choice A/B).
  기준 순위 규칙 상위 10 후보의 모든 쌍을 **두 순서 모두** 묻는다 (45 쌍 × 2 = 90 질문, 요청 하나).
* 후보 점수 = 그 후보가 든 질문에서 이길 확률의 평균. **상대 근거이지 확률이 아니다.**

평가자 입력에서 빼는 것은 172 와 같다: 기준 순위 · 정답 · ``GUIDELINE_DISTANCE`` ·
되돌림/무효화 가격 ·
``scenario_id``. 상위 10 을 고르는 데에는 기준 순위를 쓰지만(사전 걸러내기) 그 순서는 넘기지 않는다
(후보는 ``scenario_id`` 순으로 번호를 매긴다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from auto_trading.research.elliott.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    ScenarioEvaluation,
    ScenarioJudgement,
    Verdict,
)
from auto_trading.research.elliott.jev_rerank import EVALUATOR_ID, MODEL
from auto_trading.research.elliott.scenario_contract import scenario_set_digest

__all__ = [
    "PROMPT_VERSION",
    "QUESTION",
    "TOP_K",
    "build_pairwise_request",
    "narrative",
    "parse_pairwise",
    "top_k_ids",
]

PROMPT_VERSION = "ew-jev-pair/1"
TOP_K = 10
QUESTION: dict[str, Any] = {
    "question": (
        "Two candidate Elliott Wave counts of the same price move are described below as A and "
        "B. Which one is the more convincing, textbook-consistent count?"
    ),
    "guidance": (
        "Prefer counts whose legs have the internal structure their role requires (motive legs "
        "in five waves, corrective legs in three), whose retracements and wave ratios sit in the "
        "usual ranges, and whose proportions in price and time look natural."
    ),
}
"""문서 173 §2 에서 동결."""

_ROLES: dict[str, tuple[str, ...]] = {
    "IMPULSE": ("motive", "corrective", "motive", "corrective", "motive"),
    "ZIGZAG": ("motive", "corrective", "motive"),
}
_READING = {
    "IMPULSE": "verified five-wave structure",
    "ZIGZAG": "verified three-wave zigzag structure",
    "THREE": "three swings, structure not verified",
    "ATOM": "a single straight swing, too small to verify",
    "OPAQUE": "unclear internal structure",
}
_RATIO = {
    "wave2_retrace_of_wave1": ("wave 2 retraced {:.0%} of wave 1", True),
    "wave3_over_wave1": ("wave 3 is {:.2f} times wave 1", False),
    "wave4_retrace_of_wave3": ("wave 4 retraced {:.0%} of wave 3", True),
    "wave5_over_wave1": ("wave 5 is {:.2f} times wave 1", False),
    "wave4_over_wave2_duration_bars": ("wave 4 lasted {:.2f} times as long as wave 2", False),
    "b_retrace_of_a": ("wave B retraced {:.0%} of wave A", True),
    "c_over_a": ("wave C is {:.2f} times wave A", False),
}


def _ratio_text(e: Mapping[str, Any]) -> str:
    template, pct = _RATIO.get(
        str(e["key"]), (str(e["key"]).replace("_", " ") + " = {:.2f}", False)
    )
    text = template.format(float(e["observed"]))
    band = e.get("reference_band")
    if band:
        lo, hi = (f"{x:.0%}" if pct else f"{x:.2f}" for x in band)
        where = "inside" if band[0] <= float(e["observed"]) <= band[1] else "outside"
        text += f" (usual range {lo} to {hi}: {where})"
    return text


def narrative(scenario: Mapping[str, Any]) -> str:
    """후보 하나를 영어 문장으로. 숫자는 코드가 계산하고 판단 재료만 남긴다."""
    pattern = str(scenario["pattern"])
    up = int(scenario["direction"]) > 0
    anchors = scenario["anchors"]
    labels = [str(a["label"]) for a in anchors]
    name = "five-wave impulse" if pattern == "IMPULSE" else "A-B-C zigzag"
    parts = [f"{'Upward' if up else 'Downward'} {name} labelled {'-'.join(labels)}."]
    moves = []
    for k in range(1, len(anchors)):
        p0, p1 = float(anchors[k - 1]["price"]), float(anchors[k]["price"])
        hours = (
            int(anchors[k]["extreme_at_ms"]) - int(anchors[k - 1]["extreme_at_ms"])
        ) // 3_600_000
        moves.append(f"wave {labels[k]} {p1 / p0 - 1:+.1%} over {hours} h")
    parts.append("Moves: " + ", ".join(moves) + ".")
    ratios = [
        _ratio_text(e)
        for e in scenario["evidence"]
        if e["family"] in ("FIBONACCI", "ALTERNATION")
        and e["state"] == "OBSERVED"
        and e["observed"] is not None
    ]
    if ratios:
        parts.append("Ratios: " + "; ".join(ratios) + ".")
    readings = [
        str(e["source"])
        for e in scenario["evidence"]
        if e["family"] == "SUBDIVISION" and str(e["key"]).startswith("leg_")
    ]
    roles = _ROLES.get(pattern, ())
    legs = [
        f"wave {labels[k + 1]} (should be {roles[k] if k < len(roles) else 'unknown'}): "
        f"{_READING.get(r, r)}"
        for k, r in enumerate(readings)
    ]
    if legs:
        parts.append("Internal structure: " + "; ".join(legs) + ".")
    return " ".join(parts)


def top_k_ids(sidecar: Mapping[str, int], k: int = TOP_K) -> list[str]:
    """기준 순위 상위 k (동점은 scenario_id 순). 평가자에게는 **scenario_id 순**으로 준다."""
    chosen = sorted(sidecar, key=lambda sid: (sidecar[sid], sid))[:k]
    return sorted(chosen)


def build_pairwise_request(
    scenario_set: Mapping[str, Any], ids: Sequence[str], *, model: str = MODEL
) -> tuple[dict[str, Any], dict[str, tuple[str, str]]]:
    """(요청, {질문 키: (A 의 scenario_id, B 의 scenario_id)}). 모든 순서쌍."""
    by_id = {s["scenario_id"]: s for s in scenario_set["scenarios"]}
    texts = {sid: narrative(by_id[sid]) for sid in ids}
    questions: dict[str, Any] = {}
    pairs: dict[str, tuple[str, str]] = {}
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            if i == j:
                continue
            key = f"p{i:02d}{j:02d}"
            pairs[key] = (a, b)
            questions[key] = {
                "type": "choice",
                "instructions": QUESTION,
                "criteria": {"A": texts[a], "B": texts[b]},
            }
    state = "Elliott Wave count comparison. Each question compares two counts of one move."
    return {"state": state, "model": model, "questions": questions}, pairs


def parse_pairwise(
    scenario_set: Mapping[str, Any],
    pairs: Mapping[str, tuple[str, str]],
    answers: Mapping[str, Any] | None,
    *,
    evaluated_at_ms: int,
    raw: str = "",
    model: str = MODEL,
) -> ScenarioEvaluation:
    """답 → 후보별 평균 승률. 하나라도 빠지거나 확률이 이상하면 UNAVAILABLE."""

    def result(verdict: Verdict, judgements: tuple[ScenarioJudgement, ...]) -> ScenarioEvaluation:
        return ScenarioEvaluation(
            schema_version=EVALUATION_SCHEMA_VERSION,
            input_digest=scenario_set_digest(scenario_set),
            evaluator_id=EVALUATOR_ID,
            model_version=model,
            prompt_version=PROMPT_VERSION,
            evaluated_at_ms=evaluated_at_ms,
            verdict=verdict,
            judgements=judgements,
            other_or_unknown_support=None,
            raw_output=raw,
        )

    if answers is None or set(answers) != set(pairs):
        return result(Verdict.UNAVAILABLE, ())
    wins: dict[str, list[float]] = {}
    for key, (a, b) in pairs.items():
        probs = answers[key].get("probabilities") if isinstance(answers[key], Mapping) else None
        if not isinstance(probs, Mapping):
            return result(Verdict.UNAVAILABLE, ())
        pa, pb = probs.get("A"), probs.get("B")
        if not all(isinstance(x, int | float) and 0.0 <= float(x) <= 1.0 for x in (pa, pb)):
            return result(Verdict.UNAVAILABLE, ())
        wins.setdefault(a, []).append(float(pa))  # type: ignore[arg-type]
        wins.setdefault(b, []).append(float(pb))  # type: ignore[arg-type]
    judgements = tuple(
        ScenarioJudgement(sid, sum(v) / len(v), "pairwise mean win")
        for sid, v in sorted(wins.items())
    )
    return result(Verdict.RELATIVE_SUPPORT, judgements)
