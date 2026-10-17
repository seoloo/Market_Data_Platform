"""253 — 월말 종가 · 겨울/여름 수익 · 차이 · t · 판정 · 규칙 경로 (가짜 자료)."""

from __future__ import annotations

import pytest

from scripts import run_halloween_253 as m


def test_month_ends_takes_last_close() -> None:
    daily = {"2020-01-02": 1.0, "2020-01-31": 2.0, "2020-02-03": 3.0}
    assert m.month_ends(daily) == {"2020-01": 2.0, "2020-02": 3.0}


def test_seasons_and_diffs() -> None:
    me = {"2019-10": 100.0, "2020-04": 110.0, "2020-10": 99.0, "2021-04": 120.0}
    w, s = m.seasons(me, 2020) or (0.0, 0.0)
    assert w == pytest.approx(0.10) and s == pytest.approx(-0.10)
    assert m.seasons(me, 2021) is None  # 2021-10 없음
    assert m.diffs(me, 2019, 2021) == {2020: pytest.approx(0.20)}


def test_t_stat_and_judge() -> None:
    assert m.t_stat([0.1, 0.1]) == 0.0
    assert m.t_stat([0.1, 0.2, 0.3]) == pytest.approx(0.2 / (0.1 / 3**0.5))
    assert m.judge(0.02, 2.1, 9) == "지지"
    assert m.judge(0.02, 1.9, 12) == "불명"
    assert m.judge(0.02, 2.5, 8) == "불명"
    assert m.judge(-0.01, 3.0, 13) == "불리"
    assert m.judge(None, 0.0, 0) == "불리"


def test_rule_path_cash_in_summer() -> None:
    me = {f"2020-{k:02d}": 100.0 + k for k in range(4, 13)}
    me.update({f"2021-{k:02d}": 120.0 + k for k in range(1, 11)})
    bill = {"2021-04": 12.0}
    hold, rule = m.rule_path(me, bill, 2021, 2021)
    assert len(hold) == 12  # 2020-11 ~ 2021-10
    assert rule[0] == pytest.approx(hold[0])  # 11 월 보유
    assert rule[6] == pytest.approx(0.01)  # 5 월 = 4 월 말 국채 12% ÷ 12
    assert rule[7] == 0.0  # 국채 자료 없으면 0


def test_perf() -> None:
    p = m.perf([0.1, -0.5])
    assert p["mdd"] == pytest.approx(0.5)
