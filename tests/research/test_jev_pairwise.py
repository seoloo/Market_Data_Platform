from __future__ import annotations

import json

from auto_trading.research.elliott.evaluation import Verdict, validate_evaluation
from auto_trading.research.elliott.jev_pairwise import (
    build_pairwise_request,
    narrative,
    parse_pairwise,
    top_k_ids,
)
from auto_trading.research.elliott.jev_rerank import pessimistic_rank
from tests.research.test_jev_rerank import SET

SIDE = {"EW3d:ZIGZAG:a": 7, "EW3d:ZIGZAG:b": 1}


def test_narrative_is_words_without_baseline_key_or_ids() -> None:
    text = narrative(SET["scenarios"][0])
    assert text.startswith("Upward A-B-C zigzag labelled 0-A-B-C.")
    assert "wave B retraced 50% of wave A (usual range 38% to 79%: inside)" in text
    assert "wave A (should be motive): verified five-wave structure" in text
    for leak in ("0.111111", "GUIDELINE", "EW3d", "101.5", "rank", "rsi"):
        assert leak not in text


def test_top_k_hides_order_and_requests_both_orders() -> None:
    assert top_k_ids(SIDE, 1) == ["EW3d:ZIGZAG:b"]
    ids = top_k_ids(SIDE)
    assert ids == ["EW3d:ZIGZAG:a", "EW3d:ZIGZAG:b"]  # 기준 순위가 아니라 scenario_id 순
    request, pairs = build_pairwise_request(SET, ids)
    assert pairs == {
        "p0001": ("EW3d:ZIGZAG:a", "EW3d:ZIGZAG:b"),
        "p0100": ("EW3d:ZIGZAG:b", "EW3d:ZIGZAG:a"),
    }
    q = request["questions"]["p0001"]
    assert q["type"] == "choice" and set(q["criteria"]) == {"A", "B"}
    assert "1" not in json.dumps(request["state"])  # 순위 · 번호 없음


def test_parse_mean_win_and_unavailable() -> None:
    ids = top_k_ids(SIDE)
    _, pairs = build_pairwise_request(SET, ids)
    answers = {
        "p0001": {"probabilities": {"A": 0.8, "B": 0.2}},
        "p0100": {"probabilities": {"A": 0.4, "B": 0.6}},
    }
    ev = parse_pairwise(SET, pairs, answers, evaluated_at_ms=1)
    assert ev.verdict is Verdict.RELATIVE_SUPPORT and validate_evaluation(ev, SET) == []
    support = {j.scenario_id: round(j.relative_support or 0, 9) for j in ev.judgements}
    assert support == {"EW3d:ZIGZAG:a": 0.7, "EW3d:ZIGZAG:b": 0.3}
    assert pessimistic_rank(ev, "EW3d:ZIGZAG:a") == 1
    for bad in (
        None,
        {"p0001": answers["p0001"]},
        {**answers, "p0100": {"probabilities": {"A": 2}}},
    ):
        assert parse_pairwise(SET, pairs, bad, evaluated_at_ms=1).verdict is Verdict.UNAVAILABLE
