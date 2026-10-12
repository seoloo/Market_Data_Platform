"""Synthetic execution, anonymous input, failure, market exception and cache tests."""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

import pytest

from auto_trading.research import short_validation_20261013 as pure
from auto_trading.research.short_search_229 import H4, build_coin
from scripts import run_short_validation_20261013 as pilot


def bars() -> dict[int, pure.MinuteBar]:
    return {
        i * pure.MINUTE: pure.MinuteBar(
            i * pure.MINUTE, Decimal(100), Decimal(100), Decimal(100), Decimal(100), Decimal(100000)
        )
        for i in range(8640)
    }


@pytest.mark.parametrize("bounded", [True, False])
def test_flat_minute_fee_quantity_and_horizon(bounded: bool) -> None:
    r = pure.simulate_minute(bars(), [], 0, bounded=bounded)
    exit_px = Decimal(100) * Decimal("1.00045")
    assert r.status == "CLOSED" and r.entry_ms == pure.MINUTE
    assert r.exit_ms == pure.MINUTE + 12 * H4
    assert r.gross == r.filled_qty * (r.entry_px - exit_px)
    assert r.fee == r.filled_qty * (r.entry_px + exit_px) * Decimal("0.0005")
    assert r.remaining_qty == 0 and not r.partial_entry


def test_market_exception_does_not_apply_limit_price_cap() -> None:
    b = bars()
    b[pure.MINUTE] = replace(b[pure.MINUTE], open=Decimal(98), low=Decimal(98), close=Decimal(98))
    assert pure.simulate_minute(b, [], 0, bounded=True).status == "UNFILLED"
    assert pure.simulate_minute(b, [], 0, bounded=False).filled_qty > 0


def test_zero_volume_blocks_both_modes() -> None:
    b = bars()
    b[0] = replace(b[0], volume=Decimal(0))
    for bounded in (True, False):
        assert pure.simulate_minute(b, [], 0, bounded=bounded).status == "UNFILLED"


def test_entry_partial_fill_is_not_optimistic_full_fill() -> None:
    b = bars()
    b[0] = replace(b[0], volume=Decimal(10000))
    r = pure.simulate_minute(b, [], 0, bounded=True)
    assert r.filled_qty == 10 < r.desired_qty and r.partial_entry


def test_ordinary_exit_partial_pieces_preserve_all_fees() -> None:
    b = bars()
    at = pure.MINUTE + 12 * H4
    for t in (at - pure.MINUTE, at, at + pure.MINUTE):
        b[t] = replace(b[t], volume=Decimal(10000))
    r = pure.simulate_minute(b, [], 0, bounded=True)
    flat = pure.simulate_minute(bars(), [], 0, bounded=True)
    assert r.exit_pieces == 3 and r.exit_delay_min == 2
    assert r.fee == flat.fee and r.net == flat.net and r.remaining_qty == 0


def test_stop_gap_adverse_and_never_blocked_by_volume_limit() -> None:
    b = bars()
    b[10 * pure.MINUTE] = replace(
        b[10 * pure.MINUTE],
        open=Decimal(110),
        high=Decimal(110),
        low=Decimal(110),
        close=Decimal(110),
        volume=Decimal(0),
    )
    r = pure.simulate_minute(b, [], 0, bounded=True)
    assert r.stopped and r.remaining_qty == 0 and r.net < 0
    assert r.exit_ms == 10 * pure.MINUTE


def test_funding_entry_excluded_exit_included_and_mark_bounds() -> None:
    at = pure.MINUTE + 12 * H4
    funds = [
        pure.Funding(pure.MINUTE, Decimal("0.1"), Decimal(100), Decimal(90), Decimal(110), False),
        pure.Funding(at, Decimal("0.001"), Decimal(110), Decimal(100), Decimal(120), False),
    ]
    r = pure.simulate_minute(bars(), funds, 0, bounded=True)
    assert r.funding == r.filled_qty * Decimal("0.11")
    assert r.funding_worst < r.funding < r.funding_best


def test_unknown_stop_minute_omits_credits_retains_debits() -> None:
    b = bars()
    at = 10 * pure.MINUTE
    b[at] = replace(b[at], high=Decimal(110))
    funds = [
        pure.Funding(at + 30000, Decimal("0.001"), Decimal(100), Decimal(100), Decimal(100), True),
        pure.Funding(
            at + pure.MINUTE, Decimal("-0.001"), Decimal(100), Decimal(100), Decimal(100), True
        ),
    ]
    r = pure.simulate_minute(b, funds, 0, bounded=True)
    assert r.funding == -r.filled_qty * Decimal("0.1")


def test_missing_data_and_unresolved_are_not_closed_orders() -> None:
    b = bars()
    del b[20 * pure.MINUTE]
    assert pure.simulate_minute(b, [], 0, bounded=True).status == "DATA_INCOMPLETE"
    b = bars()
    for t in range(12 * H4, 36 * H4, pure.MINUTE):
        b[t] = replace(b[t], volume=Decimal(0))
    r = pure.simulate_minute(b, [], 0, bounded=True)
    assert r.status == "UNRESOLVED_MTM" and r.remaining_qty > 0


def test_double_cost_is_not_cheaper_on_flat_prices() -> None:
    assert (
        pure.simulate_minute(bars(), [], 0, bounded=True, cost=2).net
        < pure.simulate_minute(bars(), [], 0, bounded=True).net
    )


def test_unregistered_execution_parameters_rejected() -> None:
    with pytest.raises(ValueError):
        pure.simulate_minute(bars(), [], 0, bounded=True, cost=3)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"answers": {}},
        {
            "answers": {
                "edge": {
                    "choice": "positive",
                    "probabilities": {"positive": float("nan"), "nonpositive": 0},
                }
            }
        },
    ],
)
def test_invalid_prediction_is_unknown_not_neutral(raw: dict[str, object] | None) -> None:
    assert pure.probability(raw) is None


def test_probability_threshold_and_key_include_prompt_model() -> None:
    raw = {
        "answers": {
            "edge": {"choice": "positive", "probabilities": {"positive": 0.6, "nonpositive": 0.4}}
        }
    }
    assert pure.probability(raw) == pure.THRESHOLD
    assert pure.request_key("state") == pure.request_key("state") != pure.request_key("state2")


@pytest.mark.leakage
def test_anonymous_snapshot_future_independence() -> None:
    rows = [
        (
            i * H4,
            100.0 + math.sin(i / 5),
            103.0 + math.sin(i / 5),
            97.0 + math.sin(i / 5),
            100.0 + math.sin(i / 5),
            10000.0,
        )
        for i in range(300)
    ]
    funds = [(i * H4, 0.0001) for i in range(0, 300, 2)]
    full = build_coin("BTCUSDT", rows, funds)
    prefix = build_coin("BTCUSDT", rows[:221], [f for f in funds if f[0] <= 221 * H4])
    a, b = pure.snapshot_fields(full, 220), pure.snapshot_fields(prefix, 220)
    assert a == b
    text = pure.state_text(a)
    assert "BTC" not in text and "Bitcoin" not in text and "2020" not in text
    assert set(a) == set(b) and pure.state_text(a, reverse=True) != text


def test_windows_and_date_cutoff_are_bounded() -> None:
    assert pilot.month_names()[0] == "2020-06" and pilot.month_names()[-1] == "2026-08"
    assert len(pilot.month_names()) == 75
    assert pilot.merged_windows([0, H4]) == [(-pure.MINUTE, H4 + 36 * H4)]


def test_frozen_rules_and_model_complexity_not_searchable() -> None:
    assert pilot.frozen("RSI").rsi_n == 7 and pilot.frozen("RSI").regime == "R5"
    assert "KELTNER" in pilot.frozen("KELTNER").mode
    assert pure.MODEL == "english" and pure.THRESHOLD == 0.6 and pilot.CALL_CAP == 512
    with pytest.raises(ValueError):
        pilot.frozen("new_strategy")


def test_four_hour_market_and_ioc_are_independent_execution_paths() -> None:
    c = build_coin(
        "BTCUSDT", [(i * H4, 100.0, 100.0, 100.0, 100.0, 100000.0) for i in range(100)], []
    )
    c.o[36], c.lo[36] = 90, 90
    bounded = pilot.Engine([c], {c.name: []}, {}, {}, market=False)
    market = pilot.Engine([c], {c.name: []}, {}, {}, market=True)
    cfg = pilot.config("RSI", "FULL")
    assert bounded.episode(c, 35, cfg, 90 * H4) is None
    assert market.episode(c, 35, cfg, 90 * H4) is not None


def test_gap_stop_precedes_timed_market_exit() -> None:
    b = bars()
    at = pure.MINUTE + 12 * H4
    b[at] = replace(
        b[at], open=Decimal(110), high=Decimal(110), low=Decimal(110), close=Decimal(110)
    )
    r = pure.simulate_minute(b, [], 0, bounded=False)
    assert r.stopped and r.exit_ms == at and r.remaining_qty == 0
