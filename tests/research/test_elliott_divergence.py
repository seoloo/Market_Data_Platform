"""EW-0 가격 피벗/RSI 대응의 경계와 미래 무관성."""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import Swing, SwingKind, SwingSeries
from auto_trading.research.elliott.divergence import (
    DivergenceKind,
    UnknownReason,
    classify_regular_divergence,
    confirmed_divergences,
)

D = Decimal
STEP = Timeframe.H4.ms


def bar(index: int, close: str, *, low: str | None = None, high: str | None = None) -> Candle:
    price = D(close)
    return Candle(
        open_ms=index * STEP,
        close_ms=(index + 1) * STEP - 1,
        open=price,
        high=D(high) if high is not None else price + 1,
        low=D(low) if low is not None else price - 1,
        close=price,
        volume=D(1),
    )


def swing(kind: SwingKind, index: int, price: str, confirmed_index: int) -> Swing:
    return Swing(
        kind=kind,
        price=D(price),
        at_ms=index * STEP,
        confirmed_at_ms=(confirmed_index + 1) * STEP - 1,
    )


def bullish_case() -> tuple[list[Candle], SwingSeries]:
    candles = [bar(i, "100") for i in range(22)]
    candles[15] = bar(15, "90", low="90")
    candles[16] = bar(16, "95")
    candles[17] = bar(17, "100", high="103")
    candles[18] = bar(18, "102")
    candles[19] = bar(19, "95", low="89")
    candles[20] = bar(20, "100")
    swings = SwingSeries(
        swings=(
            swing(SwingKind.LOW, 15, "90", 16),
            swing(SwingKind.HIGH, 17, "103", 18),
            swing(SwingKind.LOW, 19, "89", 20),
        ),
        threshold_pct=D("0.02"),
    )
    return candles, swings


def test_regular_bullish_and_bearish_are_symmetric() -> None:
    low_1 = swing(SwingKind.LOW, 1, "100", 2)
    low_2 = swing(SwingKind.LOW, 3, "95", 4)
    high_1 = swing(SwingKind.HIGH, 1, "110", 2)
    high_2 = swing(SwingKind.HIGH, 3, "115", 4)
    assert classify_regular_divergence(low_1, low_2, D(25), D(32)) is DivergenceKind.REGULAR_BULLISH
    assert (
        classify_regular_divergence(high_1, high_2, D(75), D(68)) is DivergenceKind.REGULAR_BEARISH
    )
    assert classify_regular_divergence(low_1, low_2, D(25), D(25)) is DivergenceKind.NONE
    assert classify_regular_divergence(high_1, high_2, D(75), D(75)) is DivergenceKind.NONE
    with pytest.raises(ValueError, match="다른 종류"):
        classify_regular_divergence(low_1, high_2, D(25), D(68))


def test_adapter_emits_only_after_second_pivot_confirmation() -> None:
    candles, swings = bullish_case()
    assert confirmed_divergences(candles, swings, candles[19].close_ms) == ()
    result = confirmed_divergences(candles, swings, candles[20].close_ms)
    assert len(result) == 1
    event = result[0]
    assert event.kind is DivergenceKind.REGULAR_BULLISH
    assert event.first_rsi is not None and event.second_rsi is not None
    assert event.second_rsi > event.first_rsi
    assert event.detected_at_ms == candles[20].close_ms
    assert event.unknown_reason is None


@pytest.mark.leakage
def test_future_append_and_future_mutation_do_not_change_past_event() -> None:
    candles, swings = bullish_case()
    as_of_ms = candles[20].close_ms
    baseline = confirmed_divergences(candles[:21], swings, as_of_ms)
    mutated = [*candles[:21], bar(21, "10000", low="1", high="20000")]
    future = SwingSeries(
        swings=(*swings.swings, swing(SwingKind.HIGH, 21, "20000", 21)),
        threshold_pct=D("0.02"),
    )
    assert baseline == confirmed_divergences(mutated, future, as_of_ms)


@pytest.mark.leakage
def test_every_prefix_matches_full_input_as_of() -> None:
    candles, swings = bullish_case()
    for index in range(len(candles)):
        as_of_ms = candles[index].close_ms
        assert confirmed_divergences(candles, swings, as_of_ms) == confirmed_divergences(
            candles[: index + 1], swings, as_of_ms
        )


def test_rsi_output_is_independent_of_caller_decimal_precision() -> None:
    candles, swings = bullish_case()
    baseline = confirmed_divergences(candles, swings, candles[20].close_ms)
    with localcontext() as context:
        context.prec = 8
        assert confirmed_divergences(candles, swings, candles[20].close_ms) == baseline


def test_rsi_warmup_is_unknown_not_a_signal() -> None:
    candles = [bar(i, "100") for i in range(4)]
    candles[0] = bar(0, "100", low="99")
    candles[1] = bar(1, "101", high="102")
    candles[2] = bar(2, "100", low="98")
    swings = SwingSeries(
        swings=(
            swing(SwingKind.LOW, 0, "99", 1),
            swing(SwingKind.HIGH, 1, "102", 2),
            swing(SwingKind.LOW, 2, "98", 3),
        ),
        threshold_pct=D("0.02"),
    )
    result = confirmed_divergences(candles, swings, candles[3].close_ms)
    assert len(result) == 1
    assert result[0].kind is DivergenceKind.UNKNOWN
    assert result[0].unknown_reason is UnknownReason.RSI_WARMUP


def test_same_bar_extreme_and_confirmation_fail_closed() -> None:
    candles, swings = bullish_case()
    ambiguous = SwingSeries(
        swings=(swings.swings[0], swing(SwingKind.HIGH, 17, "103", 17), swings.swings[2]),
        threshold_pct=D("0.02"),
    )
    result = confirmed_divergences(candles, ambiguous, candles[20].close_ms)
    assert result[0].kind is DivergenceKind.UNKNOWN
    assert result[0].unknown_reason is UnknownReason.AMBIGUOUS_INTRABAR


@pytest.mark.parametrize("bad_input", ["gap", "reverse", "price", "threshold", "zero_volume"])
def test_invalid_market_or_pivot_input_is_rejected(bad_input: str) -> None:
    candles, swings = bullish_case()
    if bad_input == "gap":
        candles.pop(5)
    elif bad_input == "reverse":
        candles[5], candles[6] = candles[6], candles[5]
    elif bad_input == "price":
        swings = SwingSeries(
            swings=(*swings.swings[:2], swing(SwingKind.LOW, 19, "88", 20)),
            threshold_pct=D("0.02"),
        )
    elif bad_input == "zero_volume":
        candles[5] = Candle(
            open_ms=candles[5].open_ms,
            close_ms=candles[5].close_ms,
            open=candles[5].open,
            high=candles[5].high,
            low=candles[5].low,
            close=candles[5].close,
            volume=D(0),
        )
    else:
        swings = SwingSeries(swings=swings.swings, threshold_pct=D("0.03"))
    with pytest.raises((DataIntegrityError, ValueError)):
        confirmed_divergences(candles, swings, candles[20].close_ms)
