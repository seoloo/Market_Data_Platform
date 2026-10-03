"""QS Paper 엔진 (문서 116): 문서 110 QS·A 와 문서 112 AG-US 백테스트를 하루씩 재현하는가."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from scripts.run_ag_small import us_path
from scripts.run_qs_switch import simulate, trend_signals

from auto_trading.ops.qs_paper import QsState, settle_year, step


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2010, 1, 4)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _prices(n: int) -> tuple[list[str], dict[str, float], dict[str, float]]:
    days = _days(n)
    q, tq = {}, {}
    v, w = 100.0, 100.0
    for t, d in enumerate(days):
        r = 0.004 * math.sin(t / 37.0) + 0.0015 * math.sin(t / 5.0)
        v *= 1 + r
        w *= 1 + 3 * r
        q[d], tq[d] = v, w
    return days, q, tq


def _run_engine(days: list[str], q: dict[str, float], tq: dict[str, float], start: int) -> QsState:
    state = QsState.new(1_000.0)
    qs, ts = [q[d] for d in days], [tq[d] for d in days]
    for t in range(start, len(days)):
        last = t == len(days) - 1 or days[t + 1][:4] != days[t][:4]
        step(state, days, qs, ts, t, last_of_year=last)
    return state


def test_qs_and_a_match_doc110_backtest() -> None:
    days, q, tq = _prices(900)
    start = 200
    sig = trend_signals(days, q)
    ref_qs = simulate(days, q, tq, sig, {}, mode="QS", start=days[start], end=days[-1])
    ref_a = simulate(days, q, tq, sig, {}, mode="A", start=days[start], end=days[-1])
    state = _run_engine(days, q, tq, start)
    assert ref_qs.trades > 0, "추세가 바뀌어야 시험이 의미 있다"
    assert abs(state.books["QS"].equity[-1][1] - ref_qs.final) < 1e-3
    assert abs(state.books["A"].equity[-1][1] - ref_a.final) < 1e-3
    assert abs(state.books["QS"].tax_paid - ref_qs.tax_paid) < 1e-3


def test_ag_matches_doc112_us_path() -> None:
    days, q, tq = _prices(900)
    start = 200
    sig = trend_signals(days, q)
    ref = us_path(days, tq, sig, days[start], days[-1], 1_000.0)
    state = _run_engine(days, q, tq, start)
    assert abs(state.books["AG"].equity[-1][1] - ref[days[-1]]) < 1e-3
    assert state.books["AG"].invested == 1_000.0


def test_state_round_trip() -> None:
    days, q, tq = _prices(400)
    state = _run_engine(days, q, tq, 300)
    again = QsState.from_json(json.loads(json.dumps(state.to_json())))
    assert again == state


def test_year_end_settled_late_when_next_day_unknown() -> None:
    """12 월 마지막 날을 다음 해 자료 없이 처리해도 새해 첫날 앞에 정산하면 백테스트와 같다."""
    days, q, tq = _prices(900)
    start = 200
    qs, ts = [q[d] for d in days], [tq[d] for d in days]
    state = QsState.new(1_000.0)
    for t in range(start, len(days)):
        if state.last_date and state.last_date[:4] != days[t][:4]:
            settle_year(state)
        step(state, days, qs, ts, t, last_of_year=False)
    settle_year(state)
    ref = _run_engine(days, q, tq, start)
    assert state.books["QS"].tax_paid == ref.books["QS"].tax_paid
    assert state.books["QS"].tax_paid > 0
