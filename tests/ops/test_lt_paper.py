"""LT Paper 엔진 (문서 115): 문서 113 백테스트와 같은 매매를 하루씩 재현하는가, 상태 저장·복원."""

from __future__ import annotations

import json
from datetime import date, timedelta

from scripts.run_lt_leaders import Panel
from scripts.run_lt_leaders import simulate as backtest

from auto_trading.ops.lt_paper import PaperState, step


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2007, 9, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _panel(n: int) -> tuple[Panel, list[float | None]]:
    days = _days(n)
    close: dict[str, list[float | None]] = {}
    sector: dict[str, str] = {}
    for k in range(60):
        sym = f"S{k:02d}"
        sector[sym] = ("Tech", "Energy", "Utilities", "Health")[k % 4]
        v = 100.0
        xs: list[float | None] = []
        for t in range(n):
            # 섹터·종목마다 다른 주기의 추세 → 주도주가 바뀌고 추세가 꺾인다
            v *= 1 + 0.002 * ((t // (40 + k)) % 3 - 1) + 0.0001 * (k % 4)
            xs.append(v)
        close[sym] = xs
    spy: list[float | None] = [100.0 * (1.0003**t) for t in range(n)]
    return Panel(days, close, sector), spy


def test_engine_matches_lt_backtest_trades() -> None:
    panel, spy = _panel(700)
    spy_ok = [True] * len(panel.days)
    ref = backtest(panel, spy_ok)
    start = next(i for i, d in enumerate(panel.days) if d >= "2008-06-01")
    state = PaperState(cash=1.0, started=True)
    for t in range(start, len(panel.days)):
        step(state, panel.days, panel.close, spy, panel.sector, t)
    got = [(tr["symbol"], tr["entry"], tr["exit"], tr["reason"]) for tr in state.trades]
    want = [(tr["symbol"], tr["entry"], tr["exit"], tr["reason"]) for tr in ref.trades]
    assert want, "백테스트가 매매를 해야 시험이 의미 있다"
    assert got == want
    assert abs(state.equity[-1][1] - ref.equity[-1]) < 1e-4  # 엔진은 평가액을 소수 4 자리로 남긴다


def test_state_round_trip_json() -> None:
    panel, spy = _panel(400)
    state = PaperState(cash=1000.0)
    for t in range(300, 400):
        step(state, panel.days, panel.close, spy, panel.sector, t)
    again = PaperState.from_json(json.loads(json.dumps(state.to_json())))
    assert again == state


def test_leaders_tie_order_is_fixed() -> None:
    """동점이면 종목 · 섹터 이름 순 — 연구 판(run_lt_leaders)과 같은 결과."""
    from scripts.run_lt_leaders import leaders as research_leaders

    from auto_trading.ops.lt_paper import leaders

    days = _days(200)
    close: dict[str, list[float | None]] = {
        f"S{k:02d}": [100.0 * 1.001**t for t in range(200)] for k in range(60)
    }
    sector = {s: ("Tech", "Energy", "Utilities", "Health")[int(s[1:]) % 4] for s in close}
    lead, top = leaders(close, sector, 199)
    assert top == ["Health", "Utilities", "Energy"]  # 이름 순 점수 → 끝 번호 많은 섹터가 위
    assert [s for s, _ in lead] == [s for s, _ in research_leaders(Panel(days, close, sector), 199)]
    assert lead[0][0] == "S59" and "S56" not in dict(lead)  # S56 은 Tech(빠진 섹터)
