"""문서 274 — 순위 · 정답 · 실적 증가 · 겹침 제거 · 미리 잡기 · 판정 (순수 함수)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts import run_leader_ml_274 as m


def test_pct_rank_ties_by_ticker() -> None:
    r = m.pct_rank({"B": 1.0, "A": 1.0, "C": 3.0})
    assert r == {"A": 0.0, "B": 0.5, "C": 1.0}
    assert m.pct_rank({"X": 5.0}) == {"X": 0.5}


def test_top_labels_exact_top_ten_percent() -> None:
    fwd: dict[str, float | None] = {f"S{i:02d}": float(i) for i in range(40)}
    fwd["MISSING"] = None
    lab = m.top_labels(fwd)
    assert sum(v for v in lab.values() if v) == 4  # 40 개 중 4 개
    assert lab["S39"] == 1 and lab["S36"] == 1 and lab["S35"] == 0
    assert lab["MISSING"] is None
    assert sum(v for v in m.top_labels({f"S{i}": float(i) for i in range(5)}).values() if v) == 1


def test_growth_uses_only_filed_quarters_and_year_ago() -> None:
    qs = [
        ("2020-05-01", "2020-03-31", 100.0),
        ("2021-05-01", "2021-03-31", 130.0),
        ("2021-08-01", "2021-06-30", 999.0),
    ]
    assert m.growth(qs, "2021-06-01") == pytest.approx(0.30)
    assert m.growth(qs, "2021-04-30") is None  # 2021 분기 공시 전 — 2020 분기는 200 일 넘게 지남
    assert m.growth(qs, "2021-12-31") is None  # 2021-06 분기의 1 년 전 없음
    assert m.growth([("2020-05-01", "2020-03-31", -5.0), *qs[1:2]], "2021-06-01") is None


@pytest.mark.leakage
def test_training_rows_end_before_test_year() -> None:
    days = [f"2015-{mo:02d}-{d:02d}" for mo in range(1, 13) for d in (1, 15)] + ["2016-01-04"]
    assert m.trainable(0, days, 2016, horizon=23)  # 끝 2015-12-15
    assert not m.trainable(1, days, 2016, horizon=23)  # 끝 2016-01-04 → 쓸 수 없음
    assert not m.trainable(10, days, 2016, horizon=23)  # 끝이 자료 밖


def test_caught_counts_any_prior_list() -> None:
    assert m.caught(["A", "B", "C"], [["A"], ["X", "C"], []]) == 2


def test_nw_t_zero_for_balanced_series() -> None:
    assert m.nw_t([1.0, -1.0] * 20) == pytest.approx(0.0, abs=1e-9)
    assert m.nw_t([0.1, 0.12, 0.09, 0.11] * 10) > 2


def _res(prec: float, t: float, sharpe: float, halves: float, mdd: float) -> dict[str, Any]:
    def acc(s: float, h: float, d: float) -> dict[str, Any]:
        return {"full": {"sharpe": s, "mdd": d}, "first": {"sharpe": h}, "second": {"sharpe": h}}

    return {
        "precision": {"GBM": {"mean": prec}, "BASE": {"mean": 0.15}},
        "precision_diff": {"GBM": {"nw_t": t}},
        "account": {"GBM": acc(sharpe, halves, mdd), "BASE": acc(0.8, 0.7, 0.30)},
    }


def test_judge_needs_both_universes() -> None:
    good = _res(0.25, 2.5, 0.95, 0.8, 0.31)
    assert m.judge({"U1": good, "U2": good})["status"] == "후속 가치(탐색)"
    weak = _res(0.20, 1.0, 0.85, 0.8, 0.31)
    assert m.judge({"U1": good, "U2": weak})["status"] == "불명"
    worse = _res(0.14, 0.0, 0.85, 0.8, 0.31)
    assert m.judge({"U1": worse, "U2": good})["status"] == "불리"
    low = _res(0.20, 2.5, 0.70, 0.6, 0.31)
    assert m.judge({"U1": low, "U2": low})["status"] == "불리"
