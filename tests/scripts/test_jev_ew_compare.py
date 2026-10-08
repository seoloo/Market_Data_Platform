from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.jev_ew_compare import Budget, call, mcnemar_one_sided, request_hash, score

REQ: dict[str, Any] = {"state": {"swings": []}, "model": "jev-1.13.0", "questions": {"c00": {}}}
OK = json.dumps(
    {"model": "jev-1.13.0", "answers": {"c00": {"noul": 0.4}}, "usage": {"input_tokens": 1000}}
)


def test_call_records_usage_then_replays_cache(tmp_path: Path) -> None:
    sent: list[Any] = []

    def post(req: Any) -> tuple[int, str]:
        sent.append(req)
        return 200, OK

    budget = Budget(0.01)
    assert call(REQ, post, budget, tmp_path) == (OK, None)
    assert abs(budget.spent_usd - 4.2e-05) < 1e-12 and budget.calls == 1
    assert (tmp_path / f"{request_hash(REQ)}.json").exists()
    assert call(REQ, post, budget, tmp_path) == (OK, None)  # 캐시 — 재호출 없음
    assert len(sent) == 1 and budget.cached == 1


def test_budget_stop_before_sending(tmp_path: Path) -> None:
    def post(req: Any) -> tuple[int, str]:
        raise AssertionError("보내면 안 된다")

    budget = Budget(1e-9)
    assert call(REQ, post, budget, tmp_path) == (None, "BUDGET_STOP")
    assert budget.stopped and budget.calls == 0


def test_retries_overload_then_counts_failure_cost(tmp_path: Path) -> None:
    codes = iter([529, 429, 401])
    waits: list[float] = []

    def post(req: Any) -> tuple[int, str]:
        return next(codes), "nope"

    budget = Budget(1.0)
    assert call(REQ, post, budget, tmp_path, sleep=waits.append) == (None, "HTTP 401")
    assert budget.calls == 3 and waits == [1.0, 2.0] and budget.spent_usd > 0
    assert not list(tmp_path.glob("*.json"))  # 실패는 캐시하지 않는다


def test_mcnemar_and_score_split_recall_from_selection() -> None:
    assert mcnemar_one_sided(0, 0) == 1.0
    assert abs(mcnemar_one_sided(3, 0) - 0.125) < 1e-12
    rows: list[dict[str, Any]] = [
        {
            "triggered": True,
            "in_set": True,
            "base_rank": 1,
            "jev_rank": 2,
            "jev_verdict": "RELATIVE_SUPPORT",
        },
        {
            "triggered": True,
            "in_set": True,
            "base_rank": 3,
            "jev_rank": 1,
            "jev_verdict": "RELATIVE_SUPPORT",
        },
        {
            "triggered": True,
            "in_set": True,
            "base_rank": 5,
            "jev_rank": 1,
            "jev_verdict": "RELATIVE_SUPPORT",
        },
        {
            "triggered": True,
            "in_set": False,
            "base_rank": None,
            "jev_rank": None,
            "jev_verdict": "RELATIVE_SUPPORT",
        },
        {
            "triggered": False,
            "in_set": False,
            "base_rank": None,
            "jev_rank": None,
            "jev_verdict": None,
        },
    ]
    s = score(rows)
    assert s["recall_at_50"] == 0.6 and s["no_trigger"] == 1
    assert s["base_inset_top1"] == round(1 / 3, 4) and s["jev_inset_top1"] == round(2 / 3, 4)
    assert s["base_top1_all"] == 0.2 and s["jev_top1_all"] == 0.4
    assert (s["jev_only"], s["base_only"], s["diff_top1_all"]) == (2, 1, 0.2)
    assert s["mcnemar_p_one_sided"] == 0.5
