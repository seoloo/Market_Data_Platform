"""267 — 돌파 체결가(갭 미끄럼) · 같은 분 양쪽이면 나쁜 쪽."""

from __future__ import annotations

from scripts.run_v_exec_267 import trade


def test_fill_at_level_or_gap_open() -> None:
    day = [(0, 100.0, 101.0, 99.0, 100.0), (60_000, 100.0, 106.0, 100.0, 105.0),
           (120_000, 107.0, 108.0, 104.0, 104.0)]  # fmt: skip
    hit = trade(day, 105.0, 90.0)
    assert hit is not None and hit[:3] == (1, 105.0, 105.0)  # 분 안에서 닿음 → 기준가
    gap = trade(day[:1] + day[2:], 105.0, 90.0)
    assert gap is not None and gap[:3] == (1, 107.0, 105.0)  # 시가가 이미 위 → 갭


def test_both_sides_same_minute_takes_worse() -> None:
    day = [(0, 100.0, 110.0, 90.0, 100.0), (60_000, 100.0, 100.0, 100.0, 108.0)]
    side, *_rest, both = trade(day, 105.0, 95.0)  # type: ignore[misc]
    assert side == -1 and both  # 종가 108 → 숏이 나쁜 쪽
