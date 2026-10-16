"""247 — 공포 첫날 · 지수 기준 맞추기(F1) · 다음 날 매수 수익 · 판정 (가짜 자료)."""

from __future__ import annotations

import pytest

from scripts import run_panic_247 as m


def test_first_days_need_quiet_gap() -> None:
    flags = [False, True, True, False] + [False] * 25 + [True]
    assert m.first_days(flags) == [1, 29]
    assert m.first_days([True, False, True], gap=5) == [0]


def test_index_flags_and_choose_x() -> None:
    close = [100.0] * 30 + [92.0] + [100.0] * 30 + [95.0]
    assert m.first_days(m.index_flags(close, 0.07)) == [30]  # 92 ≤ 100 × 0.93
    best, scores = m.choose_x(close, [30])
    assert best == 0.05 and scores["0.07"] == 1.0  # 5 · 6 · 7% 모두 [30] 만 → 같으면 작은 값
    assert m.f1([30, 61], [30]) == pytest.approx(2 * 0.5 * 1 / 1.5)


def test_forward_and_judge() -> None:
    close = [100.0, 100.0, 110.0] + [120.0] * 30
    assert m.forward(close, 0, 1) == pytest.approx(110 / 100 - 1 - m.COST)
    assert m.forward(close, 30, 20) is None
    assert m.judge([0.05, 0.03, -0.01, 0.04, 0.02], 0.01) == "지지"
    assert m.judge([0.05, 0.03, -0.01, 0.04], 0.01) == "불명"  # 5 건 미만
    assert m.judge([-0.02, 0.01], 0.0) == "불리"
