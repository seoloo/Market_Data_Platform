"""WA 경계·누수·명시적 잠금 상태 회귀 검사."""

from dataclasses import replace
from decimal import Decimal
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.config.schema import WAConfig
from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features.wa import WAState, evaluate_wa

D = Decimal
HOUR = Timeframe.H1.ms


def candles(count: int = 200) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            open_ms=i * HOUR,
            close_ms=(i + 1) * HOUR - 1,
            open=D(100) - D(i % 3) / 10,
            close=D(100) - D(i % 3) / 10,
            high=D(120),
            low=D(95),
            volume=D(100),
        )
        for i in range(count)
    )


def falling_window() -> tuple[Candle, ...]:
    bars = candles(168)
    return (*bars[:-1], replace(bars[-1], open=D(99), close=D(99)))


def test_known_state_and_sample_sigma() -> None:
    bars = falling_window()
    got = evaluate_wa(
        bars, as_of_ms=bars[-1].close_ms, cfg=WAConfig(direction="long"), state=WAState()
    )
    assert got.triggered and got.available
    assert got.return_24h_pct == D(99) / bars[-25].close - 1
    assert got.range_position_pct == D("0.16")
    returns = [b.close / a.close - 1 for a, b in zip(bars[-25:-1], bars[-24:], strict=True)]
    mean = sum(returns, D(0)) / 24
    assert got.sigma_1h == (sum(((x - mean) ** 2 for x in returns), D(0)) / 23).sqrt()


def test_stop_formula_matches_original_design_measurement() -> None:
    from scripts.measure_w_candidates import stop_distance

    bars = falling_window()
    cfg = WAConfig(direction="long")
    got = evaluate_wa(bars, as_of_ms=bars[-1].close_ms, cfg=cfg, state=WAState())
    assert got.sigma_1h is not None
    actual = got.sigma_1h * D(6).sqrt() * cfg.stop_sigma_multiple
    expected = stop_distance([float(c.close) for c in bars], len(bars) - 1, 6)
    assert float(actual) == pytest.approx(expected, rel=1e-10)


@pytest.mark.leakage
def test_rolling_window_and_full_prefix_have_same_trigger_state() -> None:
    bars = candles()
    cfg = WAConfig(direction="long", control_without_state=True)
    full_state, rolling_state = WAState(), WAState()
    for i in range(167, len(bars)):
        full = evaluate_wa(bars[: i + 1], as_of_ms=bars[i].close_ms, cfg=cfg, state=full_state)
        rolling = evaluate_wa(
            bars[i - 167 : i + 1], as_of_ms=bars[i].close_ms, cfg=cfg, state=rolling_state
        )
        assert full == rolling
        full_state, rolling_state = full.state, rolling.state


@pytest.mark.parametrize("price, triggered", [(D("100"), True), (D("100.001"), False)])
def test_lower_twenty_percent_boundary(price: Decimal, triggered: bool) -> None:
    bars = list(falling_window())
    bars[-25] = replace(bars[-25], open=D(101), close=D(101))
    bars[-1] = replace(bars[-1], open=price, close=price)
    got = evaluate_wa(
        bars, as_of_ms=bars[-1].close_ms, cfg=WAConfig(direction="long"), state=WAState()
    )
    assert got.triggered is triggered


def test_zero_return_not_decline_but_control_triggers() -> None:
    bars = candles(168)
    assert bars[-1].close == bars[-25].close
    for control in (False, True):
        got = evaluate_wa(
            bars,
            as_of_ms=bars[-1].close_ms,
            cfg=WAConfig(direction="long", control_without_state=control),
            state=WAState(),
        )
        assert got.triggered is control


@pytest.mark.parametrize("elapsed, expected", [(11, False), (12, True), (13, True)])
def test_twelve_hour_lock(elapsed: int, expected: bool) -> None:
    bars = falling_window()
    now = bars[-1].close_ms
    got = evaluate_wa(
        bars,
        as_of_ms=now,
        cfg=WAConfig(direction="long"),
        state=WAState(now - HOUR, now - elapsed * HOUR),
    )
    assert got.triggered is expected


@pytest.mark.leakage
def test_future_candles_do_not_change_current_result() -> None:
    bars = candles()
    now = bars[167].close_ms
    cfg = WAConfig(direction="long", control_without_state=True)
    assert evaluate_wa(bars, as_of_ms=now, cfg=cfg, state=WAState()) == evaluate_wa(
        bars[:168], as_of_ms=now, cfg=cfg, state=WAState()
    )


@pytest.mark.parametrize("kind", ["missing", "short", "stale", "off_boundary"])
def test_bad_window_fails_closed(kind: str) -> None:
    bars = falling_window()
    now = bars[-1].close_ms
    if kind == "missing":
        bars = (*bars[:80], *bars[81:])
    elif kind == "short":
        bars = bars[1:]
    elif kind == "stale":
        now += HOUR
    else:
        now += 1
    got = evaluate_wa(bars, as_of_ms=now, cfg=WAConfig(direction="long"), state=WAState())
    assert not got.available and not got.triggered
    assert got.state == WAState()


@given(st.lists(st.integers(min_value=167, max_value=199), min_size=1, max_size=40))
def test_duplicate_and_out_of_order_evaluations_cannot_retrigger(indices: list[int]) -> None:
    bars = candles()
    cfg = WAConfig(direction="long", control_without_state=True)
    state = WAState()
    events: list[int] = []
    for index in indices:
        now = bars[index].close_ms
        got = evaluate_wa(bars, as_of_ms=now, cfg=cfg, state=state)
        if got.triggered:
            events.append(now)
        if state.last_evaluated_ms is not None and now <= state.last_evaluated_ms:
            assert got.state == state
        state = got.state
    assert all(b - a >= 12 * HOUR for a, b in pairwise(events))
