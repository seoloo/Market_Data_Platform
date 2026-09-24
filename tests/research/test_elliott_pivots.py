"""EW-0 4h 피벗: 장중 순서 모호함과 prefix 불변성."""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.divergence import detect_ew0_divergences
from auto_trading.research.elliott.pivots import detect_guarded_pivots

D = Decimal
STEP = Timeframe.H4.ms


def bar(index: int, open_: str, high: str, low: str, close: str, volume: str = "1") -> Candle:
    return Candle(
        open_ms=index * STEP,
        close_ms=(index + 1) * STEP - 1,
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(volume),
    )


def safe_path(start: int = 0) -> list[Candle]:
    return [
        bar(start, "100", "100.5", "99.5", "100"),
        bar(start + 1, "102", "103", "101", "102"),
        bar(start + 2, "100", "101", "99", "100"),
        bar(start + 3, "97", "97", "96", "97"),
        bar(start + 4, "99", "100", "98", "99"),
        bar(start + 5, "101", "102", "100", "101"),
        bar(start + 6, "98", "99", "97", "98"),
    ]


def test_prior_bar_extremes_confirm_in_order() -> None:
    candles = safe_path()
    result = detect_guarded_pivots(candles, candles[-1].close_ms)
    assert result.ambiguous_open_ms == ()
    assert len(result.segments) == 1
    assert [(s.kind, s.price, s.at_ms, s.confirmed_at_ms) for s in result.segments[0].swings] == [
        (SwingKind.LOW, D("99.5"), candles[0].open_ms, candles[1].close_ms),
        (SwingKind.HIGH, D("103"), candles[1].open_ms, candles[2].close_ms),
        (SwingKind.LOW, D("96"), candles[3].open_ms, candles[4].close_ms),
        (SwingKind.HIGH, D("102"), candles[5].open_ms, candles[6].close_ms),
    ]
    assert all(s.at_ms < s.confirmed_at_ms - STEP + 1 for s in result.segments[0].swings)


def test_exact_two_percent_reversal_is_inclusive() -> None:
    candles = [bar(0, "99", "100", "99", "99"), bar(1, "98", "99", "98", "98")]
    result = detect_guarded_pivots(candles, candles[-1].close_ms)
    assert result.segments[0].swings[0].kind is SwingKind.HIGH
    assert result.segments[0].swings[0].price == D(100)


def test_first_wide_bar_is_unknown_and_skipped() -> None:
    candles = [bar(0, "99", "101", "98", "100"), *safe_path(start=1)]
    result = detect_guarded_pivots(candles, candles[-1].close_ms)
    assert result.ambiguous_open_ms == (candles[0].open_ms,)
    assert result.segments[0].swings == ()
    assert result.segments[1].swings[0].at_ms == candles[1].open_ms


def test_new_extreme_and_reversal_same_bar_split_segments() -> None:
    first = safe_path()[:3]
    ambiguous = bar(3, "100", "105", "97", "100")
    candles = [*first, ambiguous, *safe_path(start=4)]
    result, events = detect_ew0_divergences(candles, candles[-1].close_ms)
    assert result.ambiguous_open_ms == (ambiguous.open_ms,)
    assert len(result.segments) == 2
    assert len(result.segments[0].swings) == 2
    assert len(result.segments[1].swings) == 4
    assert events
    assert all(event.first.at_ms >= candles[4].open_ms for event in events)


def test_new_high_and_drop_same_bar_are_ambiguous_too() -> None:
    candles = [*safe_path()[:2], bar(2, "102", "107", "100", "102")]
    result = detect_guarded_pivots(candles, candles[-1].close_ms)
    assert result.ambiguous_open_ms == (candles[2].open_ms,)
    assert len(result.segments[0].swings) == 1
    assert result.segments[1].swings == ()


def test_tie_keeps_earliest_extreme_time() -> None:
    candles = [
        *safe_path()[:2],
        bar(2, "100", "103", "99", "100"),
    ]
    result = detect_guarded_pivots(candles, candles[-1].close_ms)
    assert result.ambiguous_open_ms == ()
    assert result.segments[0].swings[1].at_ms == candles[1].open_ms


def test_pivot_output_is_independent_of_decimal_context() -> None:
    candles = safe_path()
    baseline = detect_guarded_pivots(candles, candles[-1].close_ms)
    with localcontext() as context:
        context.prec = 8
        assert detect_guarded_pivots(candles, candles[-1].close_ms) == baseline


@pytest.mark.leakage
def test_every_prefix_matches_full_as_of() -> None:
    candles = [*safe_path()[:3], bar(3, "100", "105", "97", "100"), *safe_path(4)]
    for index, candle in enumerate(candles):
        assert detect_guarded_pivots(candles, candle.close_ms) == detect_guarded_pivots(
            candles[: index + 1], candle.close_ms
        )
        assert detect_ew0_divergences(candles, candle.close_ms) == detect_ew0_divergences(
            candles[: index + 1], candle.close_ms
        )


@pytest.mark.leakage
def test_future_extreme_cannot_rewrite_old_result() -> None:
    candles = safe_path()
    baseline = detect_ew0_divergences(candles, candles[-1].close_ms)
    future = bar(7, "100", "200", "1", "100")
    assert detect_ew0_divergences([*candles, future], candles[-1].close_ms) == baseline


@pytest.mark.leakage
@settings(max_examples=35, deadline=None)
@given(st.lists(st.integers(min_value=80, max_value=120), min_size=2, max_size=35))
def test_random_paths_keep_confirmed_prefix(prices: list[int]) -> None:
    candles = [
        bar(i, str(price), str(price + 1), str(price - 1), str(price))
        for i, price in enumerate(prices)
    ]
    for index, candle in enumerate(candles):
        full = detect_guarded_pivots(candles, candle.close_ms)
        prefix = detect_guarded_pivots(candles[: index + 1], candle.close_ms)
        assert full == prefix
        assert len(full.segments) == len(full.ambiguous_open_ms) + 1
        for segment in full.segments:
            assert all(swing.at_ms + STEP <= swing.confirmed_at_ms for swing in segment.swings)


@pytest.mark.parametrize("failure", ["gap", "reverse", "zero_volume", "wrong_frame"])
def test_bad_market_input_fails_closed(failure: str) -> None:
    candles = safe_path()
    if failure == "gap":
        candles.pop(2)
    elif failure == "reverse":
        candles[2], candles[3] = candles[3], candles[2]
    elif failure == "zero_volume":
        candles[2] = bar(2, "100", "101", "99", "100", volume="0")
    else:
        candles[2] = Candle(
            open_ms=candles[2].open_ms,
            close_ms=candles[2].close_ms - 1,
            open=candles[2].open,
            high=candles[2].high,
            low=candles[2].low,
            close=candles[2].close,
            volume=candles[2].volume,
        )
    with pytest.raises(DataIntegrityError):
        detect_guarded_pivots(candles, candles[-1].close_ms)
