from __future__ import annotations

from datetime import date, timedelta

from scripts.run_dip_187 import (
    FWD,
    bars,
    bottom_events,
    early_maps,
    fib_events,
    fwd_returns,
    timing,
    welch_one_sided,
)


def _days(n: int) -> list[str]:
    out, d = [], date(2000, 1, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _nd(c: list[float]) -> dict[str, list]:
    days = _days(len(c))
    return {"days": days, "o": c, "h": [x * 1.01 for x in c], "l": [x * 0.99 for x in c], "c": c}


def test_timing_uses_previous_day_signal_and_charges_switches() -> None:
    days = _days(5)
    px = dict(zip(days, [100.0, 110.0, 121.0, 121.0, 100.0], strict=True))
    vals = timing(days, px, [True, True, False, False, False], 0)
    # 0 일 신호 → 1 일 종가에 들어감(수수료), 2 일 +10%, 2 일 신호 끔 → 3 일 종가에 나옴(수수료)
    assert round(vals[-1], 6) == round(0.9985 * 1.1 * 0.9985, 6)


def test_fib_first_entry_and_forward_returns() -> None:
    first = [110 - 2 * i for i in range(6)]  # 저점 100 (5 번째)
    up = [101 + i for i in range(29)]  # 고점 129
    down = [129 - 3 * i for i in range(1, 12)]
    c = [float(x) for x in first + up + down + [110.0] * 40]
    ev = fib_events(bars(_nd(c)))
    assert ev and all(0.382 <= (129 * 1.01 - c[t]) / (129 * 1.01 - 100 * 0.99) <= 0.618 for t in ev)
    r = fwd_returns(c, [ev[0], ev[0] + 1], dedupe=True)
    assert len(r) == 1  # 20 일 안의 두 번째 사건은 버림
    assert FWD == 20


def test_welch_and_bottom_gate() -> None:
    t, p = welch_one_sided([0.02, 0.03, 0.025, 0.035], [0.0, 0.01, -0.01, 0.005])
    assert t > 0 and p < 0.05
    c = [100.0] * 60 + [100 - 1.5 * i for i in range(1, 30)] + [57.0 + i for i in range(30)]
    nd = _nd(c)
    ev = bottom_events(nd)
    assert ev["S1"]  # RSI 30 회복이 바닥 뒤에 생김
    maps = early_maps(nd)
    assert any(maps["g30_S1"].values()) and set(maps) >= {"g20_S1", "g30_S4"}
