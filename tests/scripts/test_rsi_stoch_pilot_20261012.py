"""Synthetic accounting and causal checks; no real market performance calculated."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest
from scripts.run_rsi_stoch_pilot_20261012 import (
    BAND,
    FEE,
    SLIP,
    Config,
    Engine,
    combine,
    configs,
    hac_t,
    nominate,
)

from auto_trading.research.short_search_229 import H4, Coin, build_coin
from scripts import run_rsi_stoch_pilot_20261012 as pilot


def fixture_coin(highs: dict[int, float] | None = None) -> Coin:
    n = 90
    c = Coin(
        "BTCUSDT",
        [i * H4 for i in range(n)],
        [100.0] * n,
        [100.0] * n,
        [100.0] * n,
        [100.0] * n,
        [10000.0] * n,
    )
    for i, px in (highs or {}).items():
        c.h[i] = px
    c.ind = {"atr": [1.0] * n}
    c.regime = {r: [True] * n for r in ("R0", "R4", "R5")}
    return c


def fixed() -> Config:
    return Config("RSI", 14, 60, 0, 0, "R0", "24H", "0.03")


def test_grid_is_fixed_and_unique() -> None:
    grid = configs()
    assert len(grid) == len({c.key for c in grid}) == 9234
    assert Counter(c.family for c in grid) == {"RSI": 243, "STOCH": 243, "COMBO": 8748}
    assert Config("RSI", 14, 60, 0, 0, "R5", "ATR72H", "0.03") in grid
    assert Config("STOCH", 0, 0, 14, 80, "R4", "48H", "0.03") in grid


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("RSI", [False, True, False, False, False]),
        ("STOCH", [False, False, False, True, False]),
        ("AND", [False] * 5),
        ("OR", [False, True, False, True, False]),
        ("RSI_THEN_STOCH", [False, False, False, True, False]),
        ("STOCH_THEN_RSI", [False] * 5),
    ],
)
def test_combinations_have_declared_meaning(mode: str, expected: list[bool]) -> None:
    assert (
        combine([False, True, False, False, False], [False, False, False, True, False], mode)
        == expected
    )


@pytest.mark.leakage
def test_combination_prefix_is_future_independent() -> None:
    for mode in ("RSI", "STOCH", "AND", "OR", "RSI_THEN_STOCH", "STOCH_THEN_RSI"):
        r, s = [False, True, False, False], [True, False, False, True]
        assert combine(r, s, mode) == combine(r + [True] * 12, s + [True] * 12, mode)[:4]


def test_sequential_window_does_not_use_four_bars_ago() -> None:
    assert not combine(
        [True, False, False, False, False], [False, False, False, False, True], "RSI_THEN_STOCH"
    )[-1]


def test_invalid_lengths_or_mode_fail() -> None:
    with pytest.raises(ValueError):
        combine([True], [], "AND")
    with pytest.raises(ValueError):
        combine([True], [True], "unknown")


def test_flat_trade_decimal_fee_and_execution_price() -> None:
    c = fixture_coin()
    ep = Engine([c]).episode(c, 5, fixed(), 40 * H4)
    assert ep is not None
    entry, exit_px = Decimal(100) * (1 - SLIP), Decimal(100) * (1 + SLIP)
    assert ep.entry_px == entry and ep.exit_px == exit_px
    assert ep.net == (entry - exit_px - FEE * (entry + exit_px)) / entry
    assert ep.entry_i == 6 and ep.exit_i == 12 and not ep.stopped


def test_entry_gap_outside_limit_is_unfilled() -> None:
    c = fixture_coin()
    c.o[6] = float(Decimal(100) * (1 - BAND) / (1 - SLIP) - Decimal("0.001"))
    assert Engine([c]).episode(c, 5, fixed(), 40 * H4) is None


def test_zero_liquidity_entry_is_unfilled() -> None:
    c = fixture_coin()
    c.v[5] = 0
    assert Engine([c]).episode(c, 5, fixed(), 40 * H4) is None


def test_stop_is_gap_adverse_and_not_low_touch_profit() -> None:
    c = fixture_coin({8: 110})
    c.o[8] = 105
    ep = Engine([c]).episode(c, 5, fixed(), 40 * H4)
    assert ep is not None and ep.stopped and ep.exit_i == 8
    assert ep.exit_px > Decimal(105)
    assert ep.net < Decimal("-0.05")


def test_timed_exit_outside_band_delays_instead_of_market_exit() -> None:
    c = fixture_coin()
    c.o[12] = 101
    c.h[12] = 101
    ep = Engine([c]).episode(c, 5, fixed(), 40 * H4)
    assert ep is not None and not ep.stopped
    assert ep.exit_i == 13 and ep.exit_delays == 1


def test_unresolved_terminal_is_mtm_not_executed_close() -> None:
    c = fixture_coin()
    ep = Engine([c]).episode(c, 5, fixed(), 10 * H4)
    assert ep is not None and ep.unresolved and ep.exit_px == Decimal(100)


def test_funding_quantity_price_and_boundary_not_rate_sum() -> None:
    c = fixture_coin()
    c.fund_t = [6 * H4, 7 * H4, 12 * H4]
    c.fund_r = [0.1, -0.001, 0.002]
    c.c[6] = 110
    ep = Engine([c]).episode(c, 5, fixed(), 40 * H4)
    assert ep is not None
    # Entry funding excluded; next mark110 payment; exit instant mark100 credit.
    assert ep.funding == Decimal("0.09") / ep.entry_px


def test_unknown_stop_timestamp_omits_credit_but_keeps_payment() -> None:
    c = fixture_coin({8: 110})
    c.fund_t = [8 * H4 + H4 // 2, 8 * H4 + H4]
    c.fund_r = [0.02, -0.001]
    ep = Engine([c]).episode(c, 5, fixed(), 40 * H4)
    assert ep is not None and ep.funding == Decimal("-0.1") / ep.entry_px


def test_double_cost_is_not_cheaper_on_flat_prices() -> None:
    c = fixture_coin()
    engine = Engine([c])
    first = engine.episode(c, 5, fixed(), 40 * H4)
    doubled = engine.episode(c, 5, fixed(), 40 * H4, cost=2)
    assert first is not None and doubled is not None and doubled.net < first.net


@pytest.mark.leakage
def test_signal_and_daily_regime_ignore_future_prices() -> None:
    rows = [
        (i * H4, 100 + i % 19, 102 + i % 19, 98 + i % 19, 100 + i % 19, 1000.0) for i in range(1500)
    ]
    prefix = build_coin("BTCUSDT", rows[:1400], [])
    entire = build_coin("BTCUSDT", rows, [])
    for cfg in configs()[::317]:
        a, b = Engine([prefix]), Engine([entire])
        assert a.signals(prefix, cfg) == [i for i in b.signals(entire, cfg) if i < 1400]
        assert prefix.regime[cfg.regime] == entire.regime[cfg.regime][:1400]


@pytest.mark.leakage
def test_train_episode_outcome_is_bounded_before_future_crash() -> None:
    c = fixture_coin()
    first = Engine([c]).episode(c, 5, fixed(), 10 * H4)
    c.h[10] = 10000
    second = Engine([c]).episode(c, 5, fixed(), 10 * H4)
    assert first == second


def test_hac_handles_zero_activity_and_positive_samples() -> None:
    assert hac_t([0.0] * 20) == 0
    assert hac_t([0.01, -0.005, 0.02, 0.0] * 10) > 0


def test_no_eligible_candidate_is_not_called_supported() -> None:
    grid = [fixed()]
    rows = [
        {
            "key": grid[0].key,
            "trades": 100,
            "unresolved": 0,
            "weekly_hac_t": 1.0,
            "symbols": {"BTCUSDT": {"trades": 100, "mean_bps": -1}},
        }
    ]
    pick = nominate(rows, grid)
    assert pick["RSI"] == {"key": grid[0].key, "train_eligible": False}


def test_shared_wallet_reconciles_simultaneous_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = fixture_coin(), fixture_coin()
    second.name = "ETHUSDT"
    engine = Engine([first, second])
    monkeypatch.setattr(pilot, "START", 0)
    monkeypatch.setattr(pilot, "END", 90 * H4)
    monkeypatch.setattr(Engine, "signals", lambda self, coin, cfg: [5, 15, 25, 35, 45])
    result = pilot.account(engine, fixed())
    assert result["counts"]["opened"] == result["counts"]["closed"] == 10
    assert result["open_terminal"] == 0
    assert result["accounting_residual_usdt"] < 1e-8
    assert result["net_usdt"] < -result["fees_usdt"]
    assert 0 < result["max_exposure"] < 0.51


def test_quantity_uses_past_volume_not_future_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = fixture_coin(), fixture_coin()
    second.name = "ETHUSDT"
    first.v[5] = second.v[5] = 0.001
    monkeypatch.setattr(pilot, "START", 0)
    monkeypatch.setattr(pilot, "END", 90 * H4)
    monkeypatch.setattr(Engine, "signals", lambda self, coin, cfg: [5])
    result = pilot.account(Engine([first, second]), fixed())
    assert result["counts"].get("opened", 0) == 0
    assert result["counts"]["size_zero"] == 2


def test_funding_reconciles_in_shared_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = fixture_coin(), fixture_coin()
    second.name = "ETHUSDT"
    for coin in (first, second):
        coin.fund_t, coin.fund_r = [7 * H4, 12 * H4], [0.001, -0.002]
    monkeypatch.setattr(pilot, "START", 0)
    monkeypatch.setattr(pilot, "END", 90 * H4)
    monkeypatch.setattr(Engine, "signals", lambda self, coin, cfg: [5])
    result = pilot.account(Engine([first, second]), fixed())
    assert result["funding_usdt"] < 0
    assert result["accounting_residual_usdt"] < 1e-8
