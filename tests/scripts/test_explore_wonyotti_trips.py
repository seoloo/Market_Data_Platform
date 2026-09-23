"""워뇨띠 v2 왕복 재구성: 인버스 손익 부호, 반대 포지션 분할, 수수료·funding 귀속."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from scripts.explore_wonyotti_trips import Fill, build_trips

T0 = datetime(2021, 1, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_long_round_trip_inverse_pnl_and_costs() -> None:
    fills = [
        Fill(_at(0), 10_000, 10_000.0, 100),
        Fill(_at(60), -10_000, 12_500.0, 100),
    ]
    funding = [(_at(30), 50), (_at(90), 999)]
    [trip] = build_trips(fills, funding)
    # 10,000 계약 × (1/10,000 − 1/12,500) = 0.2 BTC
    assert trip.gross_btc == pytest.approx(0.2)
    assert trip.cost_xbt == 250  # 수수료 200 + 보유 중 funding 50 (청산 뒤 funding 제외)
    assert trip.side == 1
    assert trip.hours == pytest.approx(1.0)
    assert trip.max_abs == 10_000
    assert trip.entry_btc == pytest.approx(1.0)
    net = 0.2 - 250 / 100_000_000
    assert trip.net_bps == pytest.approx(net / 1.0 * 10_000)


def test_short_profits_when_price_falls() -> None:
    fills = [Fill(_at(0), -5_000, 10_000.0, 0), Fill(_at(5), 5_000, 8_000.0, 0)]
    [trip] = build_trips(fills, [])
    assert trip.side == -1
    assert trip.net_btc == pytest.approx(5_000 * (1 / 8_000 - 1 / 10_000))


def test_crossing_zero_splits_into_two_trips_with_prorated_fee() -> None:
    fills = [
        Fill(_at(0), 100, 10_000.0, 0),
        Fill(_at(10), -300, 11_000.0, 30),
        Fill(_at(20), 200, 10_500.0, 0),
    ]
    first, second = build_trips(fills, [])
    assert (first.side, second.side) == (1, -1)
    assert first.cost_xbt == 10 and second.cost_xbt == 20
    assert first.closed == _at(10) and second.opened == _at(10)
    assert second.max_abs == 200


def test_scaling_in_keeps_one_trip_until_flat() -> None:
    fills = [
        Fill(_at(0), 100, 100.0, 0),
        Fill(_at(1), 100, 110.0, 0),
        Fill(_at(2), -150, 120.0, 0),
        Fill(_at(3), -50, 120.0, 0),
    ]
    [trip] = build_trips(fills, [])
    assert trip.max_abs == 200
    assert trip.closed == _at(3)
    assert trip.entry_btc == pytest.approx(100 / 100 + 100 / 110)


def test_open_position_at_end_is_not_a_trip() -> None:
    assert build_trips([Fill(_at(0), 100, 100.0, 0)], []) == []
