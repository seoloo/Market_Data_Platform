from __future__ import annotations

from itertools import pairwise

from scripts.count_kr_patterns import BUSY, scan_stock, violations, volume_trend
from tests.research.test_patterns_lmw import DBOT, IHS


def _cal(n: int) -> list[str]:
    days = [f"20{y}{m:02d}{d:02d}" for y in (20, 21) for m in range(1, 13) for d in range(1, 29)]
    return days[:n]


def _series(shape: list[float], cal: list[str], at: int) -> dict[str, float]:
    """칸 ``at`` 가 창 끝(탐지일)이 되게 모양을 넣는다. 나머지는 잔물결."""
    px = {d: 1000.0 + (7.0 if i % 2 else 0.0) for i, d in enumerate(cal)}
    for i, x in enumerate(shape):
        px[cal[at - len(shape) + 1 + i]] = x * 10
    return px


def test_violations_flag_limit_breaches_only() -> None:
    cal = ["20200102", "20200103", "20200106", "20140102", "20140103"]
    assert violations(cal, [100.0, 131.5, 130.0, None, 100.0]) == [False, True] + [False] * 3
    assert violations(["20140102", "20140103"], [100.0, 117.0]) == [False, True]


def test_volume_trend_halves_rule() -> None:
    assert volume_trend([1.0] * 19 + [1.3] * 19) == "up"
    assert volume_trend([1.3] * 19 + [1.0] * 19) == "down"
    assert volume_trend([1.0] * 19 + [1.1] * 19) == "none"


def test_scan_finds_shape_and_is_future_invariant() -> None:
    cal = _cal(220)
    px = _series(IHS, cal, 100)
    val = dict.fromkeys(cal, 2e9)
    full, eligible = scan_stock(cal, px, val, first=cal[0], last=cal[-1])
    assert eligible > 0
    assert [(e["detect"], e["kind"]) for e in full if e["detect"] <= cal[110]] == [
        (cal[100], "IHS")
    ]
    cut = 130
    part, _ = scan_stock(cal[:cut], {d: px[d] for d in cal[:cut]}, val, first=cal[0], last=cal[-1])
    keys = ("detect", "kind", "points", "volume")
    assert [{k: e[k] for k in keys} for e in part] == [
        {k: e[k] for k in keys} for e in full if e["detect"] < cal[cut]
    ]


def test_scan_respects_liquidity_floor_and_busy_period() -> None:
    cal = _cal(300)
    px = _series(IHS, cal, 100)
    px.update({k: v for k, v in _series(DBOT, cal, 100 + BUSY - 5).items() if k > cal[100]})
    px.update({k: v for k, v in _series(DBOT, cal, 200).items() if k > cal[140]})
    thin, _ = scan_stock(cal, px, dict.fromkeys(cal, 5e8), first=cal[0], last=cal[-1])
    assert thin == []
    evs, _ = scan_stock(cal, px, dict.fromkeys(cal, 2e9), first=cal[0], last=cal[-1])
    days = [cal.index(e["detect"]) for e in evs]
    assert 100 in days and 100 + BUSY - 5 not in days and 200 in days
    assert all(b - a >= BUSY for a, b in pairwise(days))
