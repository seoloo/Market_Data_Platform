from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

import pytest
from scripts.run_cruise_range_197 import combo_stats, mdd, simulate_cruise, weights

from auto_trading.core.types import Candle
from auto_trading.ops.t2_upbit import targets

DAY = 86_400_000


def _closes(n: int, drift: float, wobble: float, phase: int) -> list[float]:
    return [
        100
        * math.exp(
            drift * k + wobble * math.sin((k + phase) / 7) + 0.02 * ((k * 7919) % 13 - 6) / 6
        )
        for k in range(n)
    ]


def _candles(xs: list[float]) -> list[Candle]:
    out = []
    for k, c in enumerate(xs):
        o = k * DAY
        p = Decimal(str(c))
        out.append(Candle(o, o + DAY - 1, p, p, p, p, Decimal(1)))
    return out


@pytest.mark.parametrize("drift", [0.004, -0.003, 0.0005])
def test_weights_match_live_targets(drift: float) -> None:
    hist = {
        "BTC": _closes(200, drift, 0.05, 0),
        "ETH": _closes(200, drift * 1.5, 0.12, 3),
        "SOL": _closes(200, -drift, 0.2, 5),
    }
    live = targets({c: _candles(v) for c, v in hist.items()}, 199 * DAY + DAY - 1)
    mine = weights(hist)
    for c in hist:
        assert mine[c] == pytest.approx(float(live[c] or 0), abs=1e-9)


def test_simulate_cruise_trend_up_then_cash_and_tax() -> None:
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(900)]
    px = {}
    for c, g in (("BTC", 1.0), ("ETH", 1.2)):
        s, p = {}, 100.0
        for i, d in enumerate(days):
            p *= 1 + g * (0.004 if i < 500 else -0.006) + (0.01 if i % 2 else -0.01)
            s[d] = p
        px[c] = s
    sim = simulate_cruise(days, px, start=days[200])
    assert sim["paid"] == 300_000.0 * len({d[:7] for d in days[200:]})
    assert sim["trades"] > 0 and sim["taxes"]
    assert mdd(sim["navs"]) < 0.4  # 내려가면 현금으로 비킨다
    no_tax = simulate_cruise(days, px, start=days[200], tax=False)
    assert no_tax["vals"][-1] >= sim["vals"][-1]


def test_combo_stats_flows_do_not_count_as_returns() -> None:
    months = [f"2001-{m:02d}" for m in range(1, 13)] + [f"2002-{m:02d}" for m in range(1, 13)]
    paid = {m: 100.0 * (i + 1) for i, m in enumerate(months)}
    flat = combo_stats([(dict(paid), paid)])  # 넣은 만큼만 있음 → 배수 1, 낙폭 0
    assert flat["x1"] == pytest.approx(1.0) and flat["mdd"] == pytest.approx(0.0)


def test_stop_blocks_buys_until_release() -> None:
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(900)]
    px = {}
    for c in ("BTC", "ETH"):
        s, p = {}, 100.0
        for i, d in enumerate(days):
            # 오름 → 하루 −40% → 다시 오름
            p *= 1.006 if i < 300 else 0.6 if i == 300 else 1.004
            p *= 1.01 if i % 2 else 0.99
            s[d] = p
        px[c] = s
    base = simulate_cruise(days, px, start=days[200])
    assert base["halts"] == 0  # 정지 없음은 197 그대로
    never = simulate_cruise(days, px, start=days[200], stop="never")
    d30 = simulate_cruise(days, px, start=days[200], stop="days30")
    assert never["halts"] == 1 and never["halted_days"] > d30["halted_days"]
    assert never["vals"][-1] < d30["vals"][-1]  # 안 풀면 다시 오를 때 못 탄다
    assert base["vals"] == simulate_cruise(days, px, start=days[200], stop=None)["vals"]
