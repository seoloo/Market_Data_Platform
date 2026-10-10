"""문서 206 — 거래소 흐름 충격: 현재 날 제외 창 · 미래 무관성 · 겹침 없는 거래 · 원형 이동 검정."""

from __future__ import annotations

import math

import pytest

from auto_trading.research.exchange_flow import (
    circular_shift_p,
    event_days,
    forward_short,
    holm,
    non_overlapping,
    series,
    zscores,
)


def test_series_kinds() -> None:
    rows = [{"in": 5.0, "out": 2.0, "sply": 100.0}, {"in": 7.0, "out": 1.0, "sply": 106.0}]
    assert series(rows, "I") == [5.0, 7.0]
    assert series(rows, "N") == [3.0, 6.0]
    assert series(rows, "R") == [None, 6.0]
    with pytest.raises(ValueError):
        series(rows, "X")


@pytest.mark.leakage
def test_zscore_window_excludes_current_day_and_future() -> None:
    x: list[float | None] = [1.0, 2.0, 3.0, 2.0, 1.0, 10.0, 0.0, 50.0]
    z = zscores(x, 5)
    win = [1.0, 2.0, 3.0, 2.0, 1.0]
    m = sum(win) / 5
    sd = math.sqrt(sum((v - m) ** 2 for v in win) / 4)
    assert z[5] == pytest.approx((10.0 - m) / sd)
    assert z[:5] == [None] * 5
    # 뒤 값을 바꿔도 앞 z 는 그대로
    y = [*x[:6], 999.0, -999.0]
    assert zscores(y, 5)[:6] == z[:6]


def test_zscore_none_in_window_skips() -> None:
    z = zscores([1.0, None, 3.0, 4.0, 5.0], 2)
    assert z[2] is None and z[3] is None and z[4] is not None


def test_events_and_forward_short() -> None:
    assert event_days([None, 2.5, 1.0, 3.0], 2.0) == [1, 3]
    entry: list[float | None] = [100.0, 110.0, 99.0, 90.0]
    assert forward_short(entry, 0, 3) == pytest.approx(0.10)
    assert forward_short(entry, 1, 3) is None


def test_non_overlapping_drops_signals_while_open() -> None:
    entry: list[float | None] = [100.0 - k for k in range(12)]
    trades = non_overlapping([0, 1, 2, 3, 5], entry, 3, 0.001, [0.0001] * 12)
    assert [t.signal for t in trades] == [0, 3]
    t0 = trades[0]
    assert t0.gross == pytest.approx(-(97.0 / 100.0 - 1))
    assert t0.cost == pytest.approx(0.002)
    assert t0.funding == pytest.approx(0.0003)
    assert t0.net == pytest.approx(t0.gross - 0.002 + 0.0003)


def test_circular_shift_detects_planted_effect_and_keeps_base() -> None:
    vals: list[float | None] = [0.0] * 50
    flags = [False] * 50
    for k in (5, 20, 35):
        vals[k], flags[k] = 1.0, True
    excess, p, base = circular_shift_p(vals, flags)
    assert base == pytest.approx(3 / 50)
    assert excess == pytest.approx(1.0 - 3 / 50)
    assert p < 0.1
    # 효과 없음 → p 가 크다
    flat: list[float | None] = [0.0] * 50
    _, p2, _ = circular_shift_p(flat, flags)
    assert p2 == 1.0


def test_holm() -> None:
    assert holm({"a": 0.001, "b": 0.02, "c": 0.5}) == {"a": True, "b": False, "c": False}
