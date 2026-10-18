"""문서 276 — 두 달마다 · 상위 N · 1 주 단위 계산 · 판정 (순수 함수)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from scripts import run_leader_small_276 as s


def test_buy_months() -> None:
    assert s.buys_this_month("2026-01-30", 2) and s.buys_this_month("2026-11-30", 2)
    assert not s.buys_this_month("2026-02-27", 2) and not s.buys_this_month("2026-12-31", 2)
    assert s.buys_this_month("2026-02-27", 1)


def test_thin_keeps_top_n_on_buy_months() -> None:
    days = ["2026-01-30", "2026-02-27", "2026-03-31"]
    got = s.thin({0: ["A", "B", "C"], 1: ["D"], 2: ["E", "F"]}, days, 2, 2)
    assert got == {0: ["A", "B"], 2: ["E", "F"]}


def test_whole_shares() -> None:
    # 자본 6,000 · 묶음 6 → 몫 1,000, 종목 2 개 → 목표 500 씩
    missed, drag = s.whole_shares(6000, 6, [600.0, 120.0])
    assert missed == 1  # 600 짜리는 1 주도 못 삼
    assert drag == pytest.approx(1 - 480 / 1000)


def _plan(g: float, b: float, h: float | None = None) -> dict[str, Any]:
    def acc(x: float, hh: float) -> dict[str, Any]:
        return {"full": {"sharpe": x}, "first": {"sharpe": hh}, "second": {"sharpe": hh}}

    return {"GBM": acc(g, h if h is not None else g), "BASE": acc(b, b)}


def _u(plans: dict[str, Any], need: Mapping[str, int | None]) -> dict[str, Any]:
    return {
        "plans": plans,
        "spy": {"full": {"sharpe": 0.9}},
        "feasibility": {p: {"needed_krw": v} for p, v in need.items()},
    }


def test_judge_picks_cheapest_passing_plan() -> None:
    plans = {
        "R10x6": _plan(1.2, 1.0),
        "R5x6": _plan(1.15, 0.9),
        "R5x3": _plan(1.10, 0.9),
        "R3x3": _plan(0.95, 0.9),  # +0.10 못 넘음
    }
    need = {"R10x6": 80_000_000, "R5x6": 50_000_000, "R5x3": 30_000_000, "R3x3": 20_000_000}
    u = _u(plans, need)
    v = s.judge({"U1": u, "U2": u})
    assert v["passed"] == ["R5x6", "R5x3"] and v["status"] == "시범 후보 R5x3"


def test_judge_none() -> None:
    plans = {p: _plan(0.8, 0.9) for p in s.PLANS}
    u = _u(plans, dict.fromkeys(s.PLANS, 10_000_000))
    assert s.judge({"U1": u, "U2": u})["status"] == "축소 불가"
