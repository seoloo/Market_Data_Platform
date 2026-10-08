from __future__ import annotations

import json
from typing import Any

from auto_trading.research.elliott.evaluation import Verdict, validate_evaluation
from auto_trading.research.elliott.jev_rerank import (
    MODEL,
    QUESTION,
    Swing,
    build_request,
    estimate_cost_usd,
    parse_response,
    pessimistic_rank,
)

H = 3_600_000


def _scenario(sid: str, starts: list[int], distance: float) -> dict[str, Any]:
    labels = ["0", "A", "B", "C"]
    return {
        "scenario_id": sid,
        "pattern": "ZIGZAG",
        "direction": 1,
        "anchors": [
            {"label": lab, "price": str(100 + i), "extreme_at_ms": t * H, "confirmed_at_ms": 0}
            for i, (lab, t) in enumerate(zip(labels, starts, strict=True))
        ],
        "evidence": [
            {
                "family": "FIBONACCI",
                "key": "b_retrace_of_a",
                "state": "OBSERVED",
                "observed": 0.5,
                "reference_band": [0.382, 0.786],
            },
            {
                "family": "MOMENTUM",
                "key": "rsi",
                "state": "NOT_COMPUTED",
                "observed": None,
                "reference_band": None,
            },
            {
                "family": "SUBDIVISION",
                "key": "leg_1_reading",
                "state": "OBSERVED",
                "observed": None,
                "reference_band": None,
                "source": "IMPULSE",
            },
            {
                "family": "FIB_LEVEL",
                "key": "whole_retrace_0.5",  # gitleaks:allow (근거 이름, 비밀 아님)
                "state": "OBSERVED",
                "observed": 101.5,
                "reference_band": None,
            },
            {
                "family": "GUIDELINE_DISTANCE",
                "key": "mean_abs_log_ratio_to_textbook",
                "state": "OBSERVED",
                "observed": distance,
                "reference_band": None,
            },
        ],
        "hard_checks": [{"rule_id": "TOP_LEVEL_HARD_RULES", "status": "PASS"}],
        "invalidation_levels": [{"price": "99"}],
    }


SWINGS = tuple(Swing("L" if i % 2 == 0 else "H", str(100 + i), i * H, i % 2 == 0) for i in range(6))
SET = {
    "schema_version": "ew-scenario-set/1",
    "scenarios": [
        _scenario("EW3d:ZIGZAG:b", [1, 2, 3, 5], 0.111111),
        _scenario("EW3d:ZIGZAG:a", [0, 2, 3, 5], 0.222222),
    ],
}


def test_request_hides_rank_truth_and_baseline_key() -> None:
    request, keymap = build_request(SET, SWINGS)
    text = json.dumps(request)
    assert request["model"] == MODEL == "jev-1.13.0"
    assert keymap == {"c00": "EW3d:ZIGZAG:a", "c01": "EW3d:ZIGZAG:b"}  # scenario_id 순 · 중립 키
    for leak in ("0.111111", "0.222222", "GUIDELINE", "EW3d", "101.5", "rank", "invalidation"):
        assert leak not in text
    cand = request["questions"]["c00"]["instructions"]["candidate"]
    assert [x["swing"] for x in cand["labels"]] == [0, 2, 3, 5]
    assert cand["leg_internal_reading"] == ["IMPULSE"]
    assert [r["name"] for r in cand["ratios"]] == ["b_retrace_of_a"]  # NOT_COMPUTED 은 빼고
    assert request["questions"]["c00"]["criteria"] == QUESTION["criteria"]
    assert request["state"]["swings"][1] == {"i": 1, "h": 1, "p": 101.0, "k": "H", "major": False}
    assert 0 < estimate_cost_usd(request) < 0.001


def test_parse_ok_and_pessimistic_rank() -> None:
    _, keymap = build_request(SET, SWINGS)
    raw = json.dumps(
        {"model": "jev-1.13.0", "answers": {"c00": {"noul": 0.7}, "c01": {"noul": 0.7}}}
    )
    ev = parse_response(SET, keymap, raw, evaluated_at_ms=1)
    assert ev.verdict is Verdict.RELATIVE_SUPPORT and validate_evaluation(ev, SET) == []
    assert ev.interpretation == "RELATIVE_NOT_PROBABILITY"
    assert pessimistic_rank(ev, "EW3d:ZIGZAG:a") == 2  # 동점은 비관적으로
    raw2 = json.dumps({"answers": {"c00": {"noul": 0.9}, "c01": {"noul": 0.2}}})
    assert (
        pessimistic_rank(parse_response(SET, keymap, raw2, evaluated_at_ms=1), "EW3d:ZIGZAG:a") == 1
    )


def test_failures_become_unavailable_not_neutral() -> None:
    _, keymap = build_request(SET, SWINGS)
    bad = [
        None,
        "not json",
        json.dumps({"answers": {"c00": {"noul": 0.5}}}),  # 하나 빠짐
        json.dumps({"answers": {"c00": {"noul": 0.5}, "c01": {"noul": 1.5}}}),  # 범위 밖
        json.dumps({"answers": {"c00": {"noul": 0.5}, "c01": {"noul": 0.5}, "c09": {"noul": 1}}}),
    ]
    for raw in bad:
        ev = parse_response(SET, keymap, raw, evaluated_at_ms=1)
        assert ev.verdict is Verdict.UNAVAILABLE and ev.judgements == ()
        assert validate_evaluation(ev, SET) == []
        assert pessimistic_rank(ev, "EW3d:ZIGZAG:a") is None
    err = parse_response(SET, keymap, None, evaluated_at_ms=1, error="BUDGET_STOP")
    assert err.verdict is Verdict.UNAVAILABLE and err.raw_output == "BUDGET_STOP"
