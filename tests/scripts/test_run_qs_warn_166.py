from __future__ import annotations

import pytest
from scripts.run_qs_warn_166 import bear_marks, breadth_warning, credit_warning, warned_line

DAYS = [f"d{i:04d}" for i in range(400)]


def test_credit_warning_turns_on_when_spread_rises() -> None:
    spread = dict.fromkeys(DAYS[:300], 2.0) | dict.fromkeys(DAYS[300:], 4.0)
    w = credit_warning(DAYS, spread)
    assert not w[DAYS[250]]
    assert w[DAYS[310]]  # 20 일 평균이 200 일 평균을 넘음
    # 빈 날은 직전 값
    gappy = {d: v for i, (d, v) in enumerate(spread.items()) if i % 3}
    assert credit_warning(DAYS, gappy)[DAYS[310]]


def test_breadth_warning_needs_all_ready_and_four_or_less_above() -> None:
    up = {d: 100 + i for i, d in enumerate(DAYS)}
    down = {d: 500 - i for i, d in enumerate(DAYS)}
    sectors = {f"S{k}": (up if k < 5 else down) for k in range(9)}
    assert not breadth_warning(DAYS, sectors)[DAYS[-1]]  # 5 개 위
    sectors = {f"S{k}": (up if k < 4 else down) for k in range(9)}
    w = breadth_warning(DAYS, sectors)
    assert w[DAYS[-1]] and not w[DAYS[150]]  # 200 일 전엔 경고 없음


def test_bear_marks_counts_and_ignores_future() -> None:
    rising = [100 * 1.001**i for i in range(400)]
    falling = rising[:300] + [rising[299] * 0.99**k for k in range(1, 101)]
    assert bear_marks(rising)[-1] == 0
    last = bear_marks(falling)[-1]
    assert last is not None and last >= 8
    assert bear_marks(rising)[:251] == [None] * 251
    assert bear_marks(falling)[:300] == bear_marks(rising)[:300]


def test_warned_line_uses_previous_day_warning() -> None:
    base = {d: 100.0 * (1.01 if i % 2 else 1.0) for i, d in enumerate(DAYS[:5])}
    warn = {DAYS[1]: True}
    price, changes = warned_line(DAYS[:5], base, {}, warn)
    # d1 → d2: 전날(d1) 경고 → 1 배 (바꾼 날 2 × 0.25%)
    r = base[DAYS[2]] / base[DAYS[1]] - 1
    assert price[DAYS[2]] == pytest.approx(price[DAYS[1]] * (1 + r) * (1 - 2 * 0.0025))
    assert changes == 2  # 3 → 1 → 3
