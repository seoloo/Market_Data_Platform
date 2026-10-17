"""257 — 주말 거스르기 수익 · 금요일 목록 · 판정 (가짜 자료)."""

from __future__ import annotations

import datetime as dt

import pytest
from scripts.run_weekend_perp_255 import COST

from scripts import run_weekend_fade_257 as m


def test_fridays_are_five_regular_weekends() -> None:
    assert m.FRIDAYS[0] == dt.date(2026, 9, 11) and m.FRIDAYS[-1] == dt.date(2026, 10, 9)
    assert all(d.weekday() == 4 for d in m.FRIDAYS)


def test_fade_goes_against_weekend_move() -> None:
    up = m.fade({"F": 100.0, "S": 102.0, "O": 101.0, "C": 101.0})
    assert up["trade"] == pytest.approx(-(101 / 102 - 1) - COST)  # 주말 상승 → 숏
    down = m.fade({"F": 100.0, "S": 98.0, "O": 99.0, "C": 99.0})
    assert down["trade"] == pytest.approx((99 / 98 - 1) - COST)  # 주말 하락 → 롱


def test_judge() -> None:
    assert m.judge([0.01, 0.02, 0.005, 0.003, -0.001]) == "통과"
    assert m.judge([0.01, 0.02, -0.005, 0.003, -0.001]) == "불명"
    assert m.judge([0.001, -0.02, 0.0, 0.0, 0.0]) == "불리"
    assert m.judge([]) == "불리"
