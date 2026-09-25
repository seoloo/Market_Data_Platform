"""Elliott 데모 셋업 (56) — 다수 합의·0.5 되돌림·손절·목표 계산."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from auto_trading.research.elliott.demo_setup import setup_from_set


def scen(i: int, state: str, direction: int, a: str = "100", b: str = "120") -> dict[str, Any]:
    return {
        "scenario_id": f"s{i}",
        "current_state": state,
        "direction": direction,
        "anchors": [
            {"price": a, "extreme_at_ms": 1},
            {"price": b, "extreme_at_ms": 99},
        ],
    }


def make(states: list[tuple[str, int]], **kw: str) -> tuple[dict[str, Any], dict[str, int]]:
    scenarios = [scen(i, s, d, **kw) for i, (s, d) in enumerate(states)]
    ranks = {s["scenario_id"]: i + 1 for i, s in enumerate(scenarios)}
    return {"as_of_ms": 5, "scenarios": scenarios}, ranks


IMP, ZZ = "WAVE_5_END_CANDIDATE", "WAVE_C_END_CANDIDATE"


def test_majority_of_top10_impulses_up_gives_long_retrace_setup() -> None:
    data, ranks = make([(IMP, 1)] * 6 + [(ZZ, 1)] * 4 + [(IMP, 1)] * 5)
    s = setup_from_set(data, ranks)
    assert s is not None and s.direction == 1 and s.agree == 6 and s.top_n == 10
    assert s.entry == Decimal("110.0")  # 0.5 되돌림
    assert s.stop == Decimal("100")  # 충격파 시작 = 2파 무효 가격
    assert s.target == Decimal("110.0") + Decimal("1.618") * 20
    assert s.impulse_pct == Decimal("0.2")


def test_only_top10_count_and_five_is_not_enough() -> None:
    data, ranks = make([(IMP, 1)] * 5 + [(ZZ, 1)] * 5 + [(IMP, 1)] * 10)
    assert setup_from_set(data, ranks) is None


def test_down_impulse_gives_short_with_stop_above() -> None:
    data, ranks = make([(IMP, -1)] * 7 + [(ZZ, -1)] * 3, a="120", b="100")
    s = setup_from_set(data, ranks)
    assert s is not None and s.direction == -1
    assert s.entry == Decimal("110.0") and s.stop == Decimal("120")
    assert s.target == Decimal("110.0") - Decimal("1.618") * 20


def test_empty_set_and_rank_order_decides_anchor() -> None:
    assert setup_from_set({"as_of_ms": 1, "scenarios": []}, {}) is None
    data, ranks = make([(IMP, 1)] * 10)
    data["scenarios"][3]["anchors"][0]["price"] = "90"
    ranks = {**ranks, "s3": 0}  # s3 가 1 등
    s = setup_from_set(data, ranks)
    assert s is not None and s.a == Decimal("90") and s.scenario_ids[0] == "s3"
