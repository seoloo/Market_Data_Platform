"""워뇨띠 v3 시장 상태: 진입 이전 값만 쓰는지(인과), 역행폭·정리 시간 계산."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from scripts.explore_wonyotti_context import Minutes, adverse_excursion, context_at
from scripts.explore_wonyotti_trips import Trip

MIN = 60_000
DAY = 1_440


def _minutes(closes: list[float]) -> Minutes:
    return Minutes(
        open_ms=[i * MIN for i in range(len(closes))],
        high=[c + 1 for c in closes],
        low=[c - 1 for c in closes],
        close=closes,
    )


def test_context_uses_only_bars_closed_before_entry() -> None:
    closes = [100.0] * (8 * DAY) + [200.0] * 10
    minutes = _minutes(closes)
    entry = 8 * DAY * MIN  # 200 봉이 막 시작한 시각
    context = context_at(minutes, [(0, 0.0001)], entry)
    assert context is not None
    assert context.ret_1h == 0.0 and context.ret_24h == 0.0  # 미래 봉(200)을 보지 않는다
    assert context.funding == pytest.approx(0.0001)
    later = context_at(minutes, [(0, 0.0001)], entry + 5 * MIN)
    assert later is not None and later.ret_1h == pytest.approx(1.0)


def test_context_requires_week_of_history() -> None:
    assert context_at(_minutes([100.0] * DAY), [], DAY * MIN) is None


def test_adverse_excursion_for_long_and_short() -> None:
    minutes = _minutes([100.0, 95.0, 102.0, 98.0])
    base = datetime(1970, 1, 1, tzinfo=UTC)
    long_trip = Trip(side=1, opened=base, closed=base + timedelta(minutes=3), open_price=100.0)
    excursion, hours = adverse_excursion(minutes, long_trip) or (0.0, 0.0)
    assert excursion == pytest.approx(0.06)  # 저가 94
    assert hours == pytest.approx(2 / 60)
    short_trip = Trip(side=-1, opened=base, closed=base + timedelta(minutes=3), open_price=100.0)
    excursion, _ = adverse_excursion(minutes, short_trip) or (0.0, 0.0)
    assert excursion == pytest.approx(0.03)  # 고가 103
