"""문서 278 — 나스닥 거르기 · 판정 (순수 함수)."""

from __future__ import annotations

from typing import Any

from scripts import run_leader_nasdaq_278 as n


def test_only_keeps_rows_and_probs_together() -> None:
    rows = [{"sym": "AAPL"}, {"sym": "JPM"}, {"sym": "MSFT"}]
    r, p = n.only(rows, [0.1, 0.9, 0.5], {"AAPL", "MSFT"})
    assert [x["sym"] for x in r] == ["AAPL", "MSFT"] and p == [0.1, 0.5]


def _u(a: float, s: float, q: float) -> dict[str, Any]:
    def acc(x: float) -> dict[str, Any]:
        return {"full": {"sharpe": x}, "first": {"sharpe": x}, "second": {"sharpe": x}}

    return {"A": {"R10x6": acc(a)}, "SP_GBM": acc(s), "QQQ": acc(q)}


def test_judge() -> None:
    assert n.judge({"N1": _u(1.2, 1.0, 0.9), "N2": _u(1.2, 1.0, 0.9)})["status"] == (
        "나스닥판 우위(탐색)"
    )
    assert n.judge({"N1": _u(0.9, 1.0, 0.9), "N2": _u(0.8, 1.0, 0.9)})["status"] == "S&P 판 유지"
    assert n.judge({"N1": _u(1.05, 1.0, 0.9), "N2": _u(0.8, 1.0, 0.9)})["status"] == "차이 불명"
