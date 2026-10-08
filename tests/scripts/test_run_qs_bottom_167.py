from __future__ import annotations

import math
from datetime import date, timedelta
from itertools import pairwise

import pytest
from scripts.run_qs_bottom_167 import OVERSOLD, TURNS, indicator_marks, simulate_ladder
from scripts.run_qs_switch import leveraged, simulate

DAYS = [(date(2000, 1, 3) + timedelta(days=k)).isoformat() for k in range(700)]


def _wave() -> dict[str, float]:
    return {d: 100 * (1 + 0.3 * math.sin(i / 40)) * (1.0003**i) for i, d in enumerate(DAYS)}


def _signal(px: dict[str, float]) -> dict[str, bool | None]:
    out: dict[str, bool | None] = {}
    for i, d in enumerate(DAYS):
        out[d] = None if i < 20 else px[d] > px[DAYS[i - 20]]
    return out


def test_ladder_without_bottom_signal_equals_qs() -> None:
    px = _wave()
    lev = leveraged(DAYS, px, {}, 3.0, 0.0084)
    sig = _signal(px)
    base = simulate(DAYS, px, lev, sig, {}, mode="QS", start=DAYS[0], end=DAYS[-1])
    lad, log = simulate_ladder(DAYS, px, lev, sig, {}, {}, start=DAYS[0], end=DAYS[-1])
    assert log == []
    assert lad.final == pytest.approx(base.final)
    assert lad.max_drawdown == pytest.approx(base.max_drawdown)
    assert lad.tax_paid == pytest.approx(base.tax_paid)


def test_ladder_buys_at_most_two_tranches_per_off_episode() -> None:
    px = _wave()
    lev = leveraged(DAYS, px, {}, 3.0, 0.0084)
    sig = _signal(px)
    bottom = dict.fromkeys(DAYS, True)
    _res, log = simulate_ladder(DAYS, px, lev, sig, {}, bottom, start=DAYS[0], end=DAYS[-1])
    buys = [e for e in log if e["action"].startswith("BUY")]
    assert buys and all(e["action"] in ("BUY1", "BUY2") for e in buys)
    # 같은 꺼짐 구간 안에서는 BUY1 다음 BUY2 만, 20 거래일 간격
    for a, b in pairwise(buys):
        if b["action"] == "BUY2":
            assert a["action"] == "BUY1"
            assert DAYS.index(b["date"]) - DAYS.index(a["date"]) >= 20


def test_indicator_marks_shape_and_no_future() -> None:
    n = 400
    close = [100 * (1 + 0.2 * math.sin(i / 25)) for i in range(n)]
    high = [c * 1.01 for c in close]
    low = [c * 0.99 for c in close]
    vol = [1e6 + 1e4 * (i % 7) for i in range(n)]
    fear = [20.0 + 10 * math.sin(i / 30) for i in range(n)]
    spread = [2.0 + 0.5 * math.sin(i / 50) for i in range(n)]
    m = indicator_marks(high, low, close, vol, fear, spread)
    assert m[:252] == [None] * 252
    last = m[-1]
    assert (
        last is not None
        and len(last[0]) == len(OVERSOLD) == 15
        and len(last[1]) == len(TURNS) == 10
    )
    # 끝을 바꿔도 앞의 표시는 같다
    close2 = close[:350] + [x * 0.5 for x in close[350:]]
    m2 = indicator_marks(
        high[:350] + [c * 1.01 for c in close2[350:]],
        low[:350] + [c * 0.99 for c in close2[350:]],
        close2,
        vol,
        fear,
        spread,
    )
    assert m2[:350] == m[:350]
