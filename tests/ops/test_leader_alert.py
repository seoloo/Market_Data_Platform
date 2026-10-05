from __future__ import annotations

from auto_trading.ops.leader_alert import WINDOW, ascending_triangle, new_entries


def test_new_entries_are_absent_from_recent_history_only() -> None:
    history = [["A", "B"], ["B", "C"], ["X"]]
    assert new_entries(["C", "D", "A", "D"], history, lookback=2) == [
        "D",
        "A",
    ]  # A 는 2 번 전보다 오래됨
    assert new_entries(["A"], [], lookback=20) == ["A"]


def _shape(
    top: float, lows: tuple[float, float, float], today: float
) -> tuple[list[float], list[float], list[float]]:
    """세 구간의 저점이 오르고 고점은 top 에서 여러 번 막히는 합성 경로."""
    third = WINDOW // 3
    highs, low_s, closes = [], [], []
    for low in lows:
        for i in range(third):
            h = top if i in (2, 12) else top * 0.95
            highs.append(h)
            low_s.append(low if i == 6 else low + 3)
            closes.append((h + low) / 2)
    highs.append(max(today, top * 0.9))
    low_s.append(today * 0.99)
    closes.append(today)
    return highs, low_s, closes


def test_ascending_triangle_forming_breakout_and_not() -> None:
    h, lo, c = _shape(100.0, (80.0, 85.0, 90.0), today=98.0)
    t = ascending_triangle(h, lo, c)
    assert t.status == "형성 중" and t.resistance == 100.0 and t.touches >= 2
    assert t.gap_to_resistance is not None and t.gap_to_resistance < 0
    h, lo, c = _shape(100.0, (80.0, 85.0, 90.0), today=103.0)
    assert ascending_triangle(h, lo, c).status == "돌파"
    h, lo, c = _shape(100.0, (90.0, 85.0, 80.0), today=98.0)  # 저점이 내려감
    assert ascending_triangle(h, lo, c).status == "아님"
    assert ascending_triangle([1.0] * 10, [1.0] * 10, [1.0] * 10).status == "자료 부족"
