"""4h 전역 고저 순서의 1m 검증과 누출·품질 경계."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.research.elliott.intrabar import (
    ExtremumOrder,
    IntrabarUnknownReason,
    audit_intrabar_extrema,
)

D = Decimal
MINUTE_MS = Timeframe.M1.ms
FOUR_HOURS_MS = Timeframe.H4.ms


def minute(
    index: int,
    *,
    high: str = "101",
    low: str = "99",
    volume: str = "1",
    close_ms_adjustment: int = 0,
) -> Candle:
    return Candle(
        open_ms=index * MINUTE_MS,
        close_ms=(index + 1) * MINUTE_MS - 1 + close_ms_adjustment,
        open=D(100),
        high=D(high),
        low=D(low),
        close=D(100),
        volume=D(volume),
    )


def outer(*, high: str = "110", low: str = "90", close_ms_adjustment: int = 0) -> Candle:
    return Candle(
        open_ms=0,
        close_ms=FOUR_HOURS_MS - 1 + close_ms_adjustment,
        open=D(100),
        high=D(high),
        low=D(low),
        close=D(100),
        volume=D(240),
    )


def minutes(high_indices: tuple[int, ...], low_indices: tuple[int, ...]) -> list[Candle]:
    return [
        minute(
            i,
            high="110" if i in high_indices else "101",
            low="90" if i in low_indices else "99",
        )
        for i in range(240)
    ]


@pytest.mark.parametrize(
    ("high_index", "low_index", "expected"),
    [
        (20, 200, ExtremumOrder.HIGH_BEFORE_LOW),
        (200, 20, ExtremumOrder.LOW_BEFORE_HIGH),
    ],
)
def test_distinct_minutes_resolve_global_extreme_order(
    high_index: int, low_index: int, expected: ExtremumOrder
) -> None:
    four_hour = outer()
    result = audit_intrabar_extrema(
        four_hour, minutes((high_index,), (low_index,)), four_hour.close_ms
    )
    assert result.order is expected
    assert result.reason is None
    assert result.first_high_minute_ms == high_index * MINUTE_MS
    assert result.first_low_minute_ms == low_index * MINUTE_MS


@pytest.mark.parametrize(
    ("high_indices", "low_indices"),
    [((20,), (20,)), ((20, 220), (100,)), ((100,), (20, 220))],
)
def test_same_minute_or_interleaved_extremes_stay_unknown(
    high_indices: tuple[int, ...], low_indices: tuple[int, ...]
) -> None:
    result = audit_intrabar_extrema(outer(), minutes(high_indices, low_indices), FOUR_HOURS_MS - 1)
    assert result.order is ExtremumOrder.UNKNOWN
    assert result.reason is IntrabarUnknownReason.EXTREMA_OVERLAP


@pytest.mark.parametrize("failure", ["missing", "reverse", "wrong_length", "zero_volume"])
def test_bad_minute_grid_or_trading_data_stays_unknown(failure: str) -> None:
    bars = minutes((20,), (200,))
    if failure == "missing":
        bars.pop(30)
    elif failure == "reverse":
        bars[30], bars[31] = bars[31], bars[30]
    elif failure == "wrong_length":
        bars[30] = minute(30, close_ms_adjustment=-1)
    else:
        bars[30] = minute(30, volume="0")
    result = audit_intrabar_extrema(outer(), bars, FOUR_HOURS_MS - 1)
    assert result.order is ExtremumOrder.UNKNOWN
    if failure == "zero_volume":
        assert result.reason is IntrabarUnknownReason.ZERO_VOLUME_MINUTE
    else:
        assert result.reason is IntrabarUnknownReason.MINUTE_GRID_INCOMPLETE
    assert result.first_high_minute_ms is None


def test_ohlc_mismatch_is_not_resolved() -> None:
    result = audit_intrabar_extrema(outer(high="111"), minutes((20,), (200,)), FOUR_HOURS_MS - 1)
    assert result.order is ExtremumOrder.UNKNOWN
    assert result.reason is IntrabarUnknownReason.OHLC_MISMATCH


def test_wrong_four_hour_frame_is_rejected() -> None:
    with pytest.raises(DataIntegrityError, match="4h"):
        audit_intrabar_extrema(
            outer(close_ms_adjustment=-1), minutes((20,), (200,)), FOUR_HOURS_MS - 1
        )


@pytest.mark.leakage
def test_unclosed_four_hour_never_exposes_partial_or_future_extrema() -> None:
    four_hour = outer()
    before_close_ms = four_hour.close_ms - 1
    a = audit_intrabar_extrema(four_hour, minutes((20,), (200,)), before_close_ms)
    b = audit_intrabar_extrema(four_hour, minutes((200,), (20,)), before_close_ms)
    assert a == b
    assert a.order is ExtremumOrder.UNKNOWN
    assert a.reason is IntrabarUnknownReason.FOUR_HOUR_NOT_CLOSED
    assert a.first_high_minute_ms is None


@pytest.mark.leakage
def test_future_minutes_outside_target_four_hour_cannot_rewrite_result() -> None:
    four_hour = outer()
    base = minutes((20,), (200,))
    expected = audit_intrabar_extrema(four_hour, base, four_hour.close_ms)
    future = [minute(240, high="1000", low="1")]
    assert audit_intrabar_extrema(four_hour, [*base, *future], four_hour.close_ms) == expected
