"""JEV × Elliott 복수 시나리오 재순위 — 연구 전용 어댑터 (IDEAS 30 · 문서 172, 순수, I/O 없음).

기존 ``ew-scenario-set/1`` 묶음을 TypeSafe System One 요청 하나로 바꾸고, 응답을
``ew-scenario-eval/1`` (:mod:`evaluation`) 로 되돌린다. 호출·예산·캐시는 스크립트가 맡는다.

* 요청 하나 = 발동 하나. ``state`` 는 그 발동의 1h 꼭짓점 목록(모든 후보 공통),
  후보마다 **같은 기준의 Noul 질문** 하나 — 질문은 서로 독립으로 평가된다(공식 문서: 병렬).
* 평가자 입력에서 뺀 것 (누수 방지, 문서 172 §3): 기준 순위·사이드카, ``GUIDELINE_DISTANCE``
  (기준 순위 규칙의 둘째 키 그 자체), ``scenario_id``(질문 키는 ``c00``… 중립 번호),
  피보나치 되돌림 가격·무효화 가격(분석 전용), 정답·생성기 label·미래 봉.
* 답이 하나라도 빠지거나 0~1 밖이면 묶음 전체를 ``UNAVAILABLE`` 로 둔다 — 빠진 값을 채우지 않는다.
* ``relative_support`` 는 Noul 값을 그대로 옮긴 **상대 근거**다.
  파동 정답 확률·상승 확률·승률이 아니다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from auto_trading.research.elliott.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    ScenarioEvaluation,
    ScenarioJudgement,
    Verdict,
)
from auto_trading.research.elliott.scenario_contract import scenario_set_digest

__all__ = [
    "EVALUATOR_ID",
    "EXCLUDED_EVIDENCE",
    "MODEL",
    "PRICE_PER_INPUT_TOKEN_USD",
    "PROMPT_VERSION",
    "QUESTION",
    "Swing",
    "build_request",
    "estimate_cost_usd",
    "parse_response",
    "pessimistic_rank",
    "question_digest",
]

EVALUATOR_ID = "typesafe-jev"
MODEL = "jev-1.13.0"
"""고정 버전 ID (별칭 ``jev-latest`` 를 쓰지 않는다 — 문서 172 §5)."""
PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1_000_000
"""2026-10-08 공식 Models 문서: 입력 토큰만 과금, 출력 무료."""
PROMPT_VERSION = "ew-jev-noul/1"
EXCLUDED_EVIDENCE = frozenset({"GUIDELINE_DISTANCE", "FIB_LEVEL"})

QUESTION: dict[str, Any] = {
    "type": "noul",
    "instructions": {
        "task": (
            "The state lists the price swings (alternating highs and lows) of one market in "
            "time order. Judge the single candidate Elliott Wave labelling given below."
        ),
        "question": (
            "Is this candidate the correct Elliott Wave count of the most recent completed "
            "move — do its labelled swings mark the real wave boundaries?"
        ),
    },
    "criteria": {
        "true": (
            "Every label sits on a major swing that clearly ends a wave; the swings between "
            "two labels fit that leg's role (motive legs unfold in five parts, corrective legs "
            "in three); no larger turning point inside a leg is left unlabelled; the "
            "proportions are typical for the pattern."
        ),
        "false": (
            "A label sits on a minor swing, a larger turning point inside a leg is ignored, the "
            "swings inside a leg contradict its role, or the count starts or ends at the wrong "
            "swing."
        ),
    },
}
"""문서 172 §2 에서 동결. 바꾸면 PROMPT_VERSION 을 올리고 확인 시험을 섞지 않는다."""


@dataclass(frozen=True, slots=True)
class Swing:
    """평가자에게 보여 줄 꼭짓점 하나 (발동 시점까지 확정된 1h 꼭짓점)."""

    kind: str
    """``H`` 또는 ``L``."""
    price: str
    hour_open_ms: int
    four_hour_visible: bool


def question_digest() -> str:
    payload = json.dumps(QUESTION, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _num(text: str) -> float:
    return float(f"{float(text):.6g}")


def _state(swings: Sequence[Swing]) -> dict[str, Any]:
    t0 = swings[0].hour_open_ms if swings else 0
    return {
        "description": (
            "Swings in time order. h = hours since the first swing, p = price, "
            "k = H (swing high) or L (swing low), major = also visible on 4-hour bars."
        ),
        "swings": [
            {
                "i": i,
                "h": (s.hour_open_ms - t0) // 3_600_000,
                "p": _num(s.price),
                "k": s.kind,
                "major": s.four_hour_visible,
            }
            for i, s in enumerate(swings)
        ],
    }


def _candidate(scenario: Mapping[str, Any], index_of: Mapping[int, int]) -> dict[str, Any]:
    ratios = [
        {
            "name": e["key"],
            "value": e["observed"],
            "typical_range": e["reference_band"],
        }
        for e in scenario["evidence"]
        if e["family"] in ("FIBONACCI", "ALTERNATION")
        and e["state"] == "OBSERVED"
        and e["observed"] is not None
    ]
    legs = [
        str(e["source"])
        for e in scenario["evidence"]
        if e["family"] == "SUBDIVISION" and e["key"].startswith("leg_")
    ]
    return {
        "pattern": scenario["pattern"],
        "direction": "up" if int(scenario["direction"]) > 0 else "down",
        "labels": [
            {"label": a["label"], "swing": index_of[int(a["extreme_at_ms"])], "p": _num(a["price"])}
            for a in scenario["anchors"]
        ],
        "leg_internal_reading": legs,
        "ratios": ratios,
        "top_level_rules": [c["status"] for c in scenario["hard_checks"]],
    }


def build_request(
    scenario_set: Mapping[str, Any], swings: Sequence[Swing], *, model: str = MODEL
) -> tuple[dict[str, Any], dict[str, str]]:
    """(요청 JSON, {질문 키: scenario_id}). 후보는 scenario_id 순 — 순위 정보가 없다."""
    index_of = {s.hour_open_ms: i for i, s in enumerate(swings)}
    keymap: dict[str, str] = {}
    questions: dict[str, Any] = {}
    for k, scenario in enumerate(sorted(scenario_set["scenarios"], key=lambda s: s["scenario_id"])):
        key = f"c{k:02d}"
        keymap[key] = str(scenario["scenario_id"])
        questions[key] = {
            **QUESTION,
            "instructions": {
                **QUESTION["instructions"],
                "candidate": _candidate(scenario, index_of),
            },
        }
    return {"state": _state(swings), "model": model, "questions": questions}, keymap


def estimate_cost_usd(request: Mapping[str, Any]) -> float:
    """보수적 사전 추정 — 문자 2 개를 토큰 1 개로 (G0 실측: 약 2.4 자 = 1 토큰). 실측은 usage 로."""
    chars = len(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
    return chars / 2 * PRICE_PER_INPUT_TOKEN_USD


def parse_response(
    scenario_set: Mapping[str, Any],
    keymap: Mapping[str, str],
    raw: str | None,
    *,
    evaluated_at_ms: int,
    error: str | None = None,
) -> ScenarioEvaluation:
    """원응답 → 평가 결과. 실패·형식 오류·누락은 ``UNAVAILABLE`` (중립 값으로 채우지 않는다)."""
    digest = scenario_set_digest(scenario_set)

    def unavailable(why: str, model: str = MODEL) -> ScenarioEvaluation:
        return ScenarioEvaluation(
            schema_version=EVALUATION_SCHEMA_VERSION,
            input_digest=digest,
            evaluator_id=EVALUATOR_ID,
            model_version=model,
            prompt_version=PROMPT_VERSION,
            evaluated_at_ms=evaluated_at_ms,
            verdict=Verdict.UNAVAILABLE,
            judgements=(),
            other_or_unknown_support=None,
            raw_output=raw or why,
        )

    if error is not None or raw is None:
        return unavailable(error or "응답 없음")
    try:
        body = json.loads(raw)
        answers = body["answers"]
        model = str(body.get("model") or MODEL)
    except (ValueError, KeyError, TypeError):
        return unavailable("형식 오류")
    if set(answers) != set(keymap):
        return unavailable("질문 키와 답 키가 다르다", model)
    judgements = []
    for key, scenario_id in sorted(keymap.items()):
        value = answers[key].get("noul") if isinstance(answers[key], Mapping) else None
        if not isinstance(value, int | float) or not 0.0 <= float(value) <= 1.0:
            return unavailable(f"{key}: noul 값이 없거나 0~1 밖", model)
        judgements.append(ScenarioJudgement(scenario_id, float(value), "noul"))
    return ScenarioEvaluation(
        schema_version=EVALUATION_SCHEMA_VERSION,
        input_digest=digest,
        evaluator_id=EVALUATOR_ID,
        model_version=model,
        prompt_version=PROMPT_VERSION,
        evaluated_at_ms=evaluated_at_ms,
        verdict=Verdict.RELATIVE_SUPPORT,
        judgements=tuple(judgements),
        other_or_unknown_support=None,
        raw_output=raw,
    )


def pessimistic_rank(evaluation: ScenarioEvaluation, scenario_id: str) -> int | None:
    """정답의 순위 = 1 + (값이 같거나 큰 다른 후보 수). 평가가 없거나 정답이 없으면 None."""
    if evaluation.verdict is not Verdict.RELATIVE_SUPPORT:
        return None
    support = {j.scenario_id: j.relative_support for j in evaluation.judgements}
    mine = support.get(scenario_id)
    if mine is None:
        return None
    return 1 + sum(
        1 for k, v in support.items() if k != scenario_id and v is not None and v >= mine
    )
