"""255 — 주말 고르기(휴장 빼기) · 시각(서머타임) · F/S/O/C · 거래 수익 · 판정 (가짜 자료)."""

from __future__ import annotations

import datetime as dt

import pytest

from scripts import run_weekend_perp_255 as m


def test_weekends_skip_holidays() -> None:
    got = m.weekends(dt.date(2026, 5, 18), dt.date(2026, 6, 8))
    # 05-22 (월 05-25 휴장) 빠짐, 05-29 · 06-05 남음
    assert got == [dt.date(2026, 5, 29), dt.date(2026, 6, 5)]


def test_et_ms_follows_dst() -> None:
    winter = m.et_ms(dt.date(2026, 2, 6), 15, 30)
    summer = m.et_ms(dt.date(2026, 8, 7), 15, 30)
    assert winter == m.utc_ms(dt.date(2026, 2, 6), 20, 30)
    assert summer == m.utc_ms(dt.date(2026, 8, 7), 19, 30)


def test_points_and_obs() -> None:
    fri = dt.date(2026, 8, 7)
    sun, mon = dt.date(2026, 8, 9), dt.date(2026, 8, 10)
    bars = {
        m.utc_ms(fri, 19, 30): (99.0, 100.0),
        m.utc_ms(sun, 19, 30): (101.0, 102.0),
        m.utc_ms(mon, 13, 30): (103.0, 104.0),
        m.utc_ms(mon, 19, 30): (105.0, 106.0),
    }
    p = m.points(bars, fri)
    assert p == {"F": 100.0, "S": 102.0, "O": 103.0, "C": 106.0}
    o = m.obs(p)
    assert o["w"] == pytest.approx(0.02)
    assert o["trade"] == pytest.approx(103 / 102 - 1 - m.COST)
    down = m.obs({"F": 100.0, "S": 98.0, "O": 97.0, "C": 97.0})
    assert down["trade"] == pytest.approx(-(97 / 98 - 1) - m.COST)  # 주말 하락 → 숏
    del bars[m.utc_ms(mon, 13, 30)]
    assert m.points(bars, fri) is None


def test_judge() -> None:
    net = [0.002, 0.003, 0.001, 0.004, 0.002, 0.003]
    assert m.judge(0.003, net, net[:3], net[3:]) == "지지"
    assert m.judge(0.003, net, [-0.01], net[3:]) == "불명"
    assert m.judge(0.001, [0.001, -0.002, 0.0005], [0.001], [0.0]) == "불명"
    assert m.judge(-0.0001, net, net, net) == "불리"
