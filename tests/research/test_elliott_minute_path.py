"""1m 반전 경로는 4h 마감 후에만 보이며 불명 구간을 건너지 않는다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.intrabar import IntrabarUnknownReason
from auto_trading.research.elliott.minute_path import (
    MinutePathPivot,
    MinutePathReplay,
    replay_four_hour_minute_path,
)

D = Decimal
MINUTE_MS = Timeframe.M1.ms


def minute(
    index: int,
    price: str,
    *,
    high: str | None = None,
    low: str | None = None,
    volume: str = "1",
) -> Candle:
    return Candle(
        open_ms=index * MINUTE_MS,
        close_ms=(index + 1) * MINUTE_MS - 1,
        open=D(price),
        high=D(high) if high is not None else D(price),
        low=D(low) if low is not None else D(price),
        close=D(price),
        volume=D(volume),
    )


def group(start: int, prices: list[str]) -> list[Candle]:
    assert len(prices) == 240
    return [minute(start + offset, price) for offset, price in enumerate(prices)]


def outer(bars: list[Candle]) -> Candle:
    return Candle(
        open_ms=bars[0].open_ms,
        close_ms=bars[-1].close_ms,
        open=bars[0].open,
        high=max(bar.high for bar in bars),
        low=min(bar.low for bar in bars),
        close=bars[-1].close,
        volume=sum((bar.volume for bar in bars), start=D(0)),
    )


def priced_path() -> tuple[Candle, list[Candle]]:
    prices = ["100"] * 10 + ["103"] * 10 + ["100"] * 10 + ["97"] * 210
    bars = group(0, prices)
    return outer(bars), bars


def all_pivots(replay: MinutePathReplay) -> list[MinutePathPivot]:
    return [pivot for segment in replay.segments for pivot in segment]


@pytest.mark.leakage
def test_minute_crossings_are_hidden_until_four_hour_close() -> None:
    four_hour, bars = priced_path()
    before = replay_four_hour_minute_path(
        [four_hour], {four_hour.open_ms: bars}, four_hour.close_ms - 1
    )
    assert all_pivots(before) == []
    after = replay_four_hour_minute_path([four_hour], {four_hour.open_ms: bars}, four_hour.close_ms)
    pivots = all_pivots(after)
    assert len(pivots) >= 2
    assert pivots[0].kind is SwingKind.LOW
    assert pivots[1].kind is SwingKind.HIGH
    assert pivots[0].confirmation_minute_close_ms < four_hour.close_ms
    assert all(pivot.available_at_ms == four_hour.close_ms for pivot in pivots)


def test_ambiguous_minute_splits_path_even_when_four_hour_extrema_are_ordered() -> None:
    bars = group(0, ["100"] * 240)
    bars[10] = minute(10, "101", high="103", low="100")
    bars[50] = minute(50, "105")
    bars[200] = minute(200, "95")
    four_hour = outer(bars)
    result = replay_four_hour_minute_path(
        [four_hour], {four_hour.open_ms: bars}, four_hour.close_ms
    )
    assert result.blocked_four_hours == ()
    assert result.ambiguous_minute_open_ms == (bars[10].open_ms,)
    assert len(result.segments) == 2


def test_candidate_carries_across_complete_four_hour_boundary() -> None:
    first_minutes = group(0, ["100"] * 240)
    first_minutes[10] = minute(10, "100", high="100.5", low="100")
    first_minutes[20] = minute(20, "100", high="100", low="99.5")
    second_minutes = group(240, ["100"] * 10 + ["103"] * 230)
    first, second = outer(first_minutes), outer(second_minutes)
    mapping = {first.open_ms: first_minutes, second.open_ms: second_minutes}

    at_first_close = replay_four_hour_minute_path([first, second], mapping, first.close_ms)
    assert all_pivots(at_first_close) == []
    at_second_close = replay_four_hour_minute_path([first, second], mapping, second.close_ms)
    pivots = all_pivots(at_second_close)
    assert len(pivots) == 1
    assert pivots[0].kind is SwingKind.LOW
    assert pivots[0].extreme_minute_ms == first_minutes[20].open_ms
    assert pivots[0].available_at_ms == second.close_ms


def test_missing_minute_blocks_four_hour_and_breaks_state() -> None:
    first_minutes = group(0, ["100"] * 240)
    first_minutes[10] = minute(10, "100", high="100.5")
    first_minutes[20] = minute(20, "100", low="99.5")
    second_minutes = group(240, ["100"] * 240)
    second_minutes[30] = minute(270, "100", high="101")
    second_minutes[50] = minute(290, "100", low="99")
    third_minutes = group(480, ["100"] * 10 + ["103"] * 230)
    first, second, third = outer(first_minutes), outer(second_minutes), outer(third_minutes)
    result = replay_four_hour_minute_path(
        [first, second, third],
        {
            first.open_ms: first_minutes,
            second.open_ms: second_minutes[:30] + second_minutes[31:],
            third.open_ms: third_minutes,
        },
        third.close_ms,
    )
    assert result.blocked_four_hours[0].open_ms == second.open_ms
    assert result.blocked_four_hours[0].reason is IntrabarUnknownReason.MINUTE_GRID_INCOMPLETE
    assert all(p.extreme_minute_ms >= third.open_ms for p in all_pivots(result))


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        (minute(20, "100", volume="0"), IntrabarUnknownReason.ZERO_VOLUME_MINUTE),
        (minute(20, "100", high="110"), IntrabarUnknownReason.OHLC_MISMATCH),
    ],
)
def test_unverified_minute_blocks_entire_four_hour(
    replacement: Candle, reason: IntrabarUnknownReason
) -> None:
    four_hour, bars = priced_path()
    bars[20] = replacement
    result = replay_four_hour_minute_path(
        [four_hour], {four_hour.open_ms: bars}, four_hour.close_ms
    )
    assert result.blocked_four_hours[0].reason is reason
    assert all_pivots(result) == []


def test_overlapping_global_extrema_block_without_inventing_order() -> None:
    bars = group(0, ["100"] * 240)
    bars[10] = minute(10, "100", high="103", low="97")
    four_hour = outer(bars)
    result = replay_four_hour_minute_path(
        [four_hour], {four_hour.open_ms: bars}, four_hour.close_ms
    )
    assert result.blocked_four_hours[0].reason is IntrabarUnknownReason.EXTREMA_OVERLAP
    assert all_pivots(result) == []


@pytest.mark.leakage
def test_future_four_hour_and_minute_mutation_cannot_change_prior_snapshot() -> None:
    first, first_minutes = priced_path()
    second_minutes = group(240, ["100"] * 10 + ["120"] * 230)
    second = outer(second_minutes)
    baseline = replay_four_hour_minute_path([first], {first.open_ms: first_minutes}, first.close_ms)
    extended = replay_four_hour_minute_path(
        [first, second],
        {first.open_ms: first_minutes, second.open_ms: second_minutes},
        first.close_ms,
    )
    assert baseline == extended
