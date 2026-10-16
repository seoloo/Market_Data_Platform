"""252 — 하락 · 반등 사건 · 덜 빠진 종목 고르기 · 다음 수익 · 판정 (가짜 자료)."""

from __future__ import annotations

import pytest
from scripts.run_lt_kr import KrPanel

from scripts import run_bear_rs_252 as m


def test_find_events_peak_trough_confirm_and_reset() -> None:
    close = [100.0, 105.0, 99.0, 94.0, 90.0, 95.0, 99.5, 100.0, 95.0, 89.0, 98.0]
    # 고점 1(105) → 94 ≤ 94.5 → 저점 4(90) → 99.5 ≥ 99 = C 6
    # 새 고점 7(100) → 89 ≤ 90 = 저점 9 → 98 ≥ 97.9 = C 10
    assert m.find_events(close) == [m.Event(1, 4, 6), m.Event(7, 9, 10)]
    assert m.find_events([100.0, 95.0, 92.0, 100.0]) == []  # −10% 안 됨


def _panel(close: dict[str, list[float | None]]) -> KrPanel:
    n = len(next(iter(close.values())))
    days = [f"2020-01-{i + 1:02d}" for i in range(n)]
    return KrPanel(
        days,
        close,
        {s: [1.0] * n for s in close},
        dict.fromkeys(close, "x"),
        dict.fromkeys(close, 0),
        dict.fromkeys(close, n - 1),
    )


def test_groups_and_forward_with_stop_trading() -> None:
    panel = _panel(
        {
            "A": [100.0, 95.0, 96.0, 100.0, 120.0],  # 하락 −5% (덜 빠짐)
            "B": [100.0, 70.0, 75.0, 80.0, None],  # 하락 −30%, 끝에 거래 끊김 → 마지막 80
            "C": [100.0, 80.0, 85.0, None, 90.0],  # C+1 종가 없음 → 제외
        }
    )
    ev = m.Event(0, 1, 2)
    old = m.TOP
    m.TOP = 1
    try:
        g = m.groups(panel, ["A", "B", "C"], ev)
    finally:
        m.TOP = old
    assert g == {"strong": ["A"], "weak": ["B"], "all": ["A", "B"]}
    assert m.fwd(panel, "A", 2, 1) == pytest.approx(120 / 100 - 1 - m.COST)
    assert m.fwd(panel, "B", 2, 1) == pytest.approx(80 / 80 - 1 - m.COST)
    assert m.fwd(panel, "C", 2, 1) is None
    assert m.mean_fwd(panel, ["A", "B", "C"], 2, 1) == pytest.approx(0.1 - m.COST)


def test_judge() -> None:
    good = [0.05, 0.02, -0.01, 0.03, 0.04, 0.01]
    assert m.judge(good, good[:3], good[3:]) == "지지"
    assert m.judge(good[:5], good[:3], good[3:5]) == "불명"  # 사건 6 개 미만
    assert m.judge(good, [-0.05, -0.01], good) == "불명"  # 앞 반쪽 −
    assert m.judge([0.05, -0.02, -0.01, -0.01, 0.04, -0.01], [0.05], [0.04]) == "불명"  # 이긴 비율
    assert m.judge([-0.01, 0.005], [], []) == "불리"
