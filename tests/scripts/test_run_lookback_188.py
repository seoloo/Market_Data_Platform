from __future__ import annotations

from datetime import date, timedelta

from scripts.run_brake_183 import signals
from scripts.run_lookback_188 import L120, trend_map, verdict


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2000, 1, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_trend_map_matches_183_for_20_60_120() -> None:
    days = _days(400)
    px = {d: 100 + 10 * ((i // 37) % 3) - 0.05 * i for i, d in enumerate(days)}
    _, t183 = signals(days, px)
    assert trend_map(days, px, L120) == t183


def test_verdict_needs_both_nasdaq_and_17_markets() -> None:
    nq = {
        s: {"L120": {"irr_liq": 0.2, "mdd": 0.5}, "L100": {"irr_liq": 0.21, "mdd": 0.52}}
        for s in ("R", "S")
    }
    good = {
        str(i): {"L120": {"cagr": 0.1, "mdd": 0.5}, "L100": {"cagr": 0.11, "mdd": 0.5}}
        for i in range(17)
    }
    bad = {
        str(i + 17): {"L120": {"cagr": 0.1, "mdd": 0.5}, "L100": {"cagr": 0.0, "mdd": 0.5}}
        for i in range(8)
    }
    assert verdict(nq, {**good, **bad})["choice"] == "L100"
    nq["S"]["L100"]["irr_liq"] = 0.19
    assert verdict(nq, {**good, **bad})["choice"] == "L120"
