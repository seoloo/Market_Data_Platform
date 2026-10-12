"""Synthetic expectations, registered-grid contract, causal feature and ledger parity checks."""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

import pytest

from auto_trading.research import complement_20261013 as roles
from auto_trading.research.short_search_229 import H4, Coin, build_coin
from scripts import run_complement_pilot_20261013 as pilot
from scripts import run_rsi_stoch_pilot_20261012 as base


def coin(n: int = 420) -> Coin:
    bars = []
    for i in range(n):
        p = 100 + 5 * math.sin(i / 7) + 0.01 * i
        bars.append((i * H4, p, p + 1, p - 1, p + 0.2 * math.cos(i), 10000 + 100 * (i % 10)))
    c = build_coin("BTCUSDT", bars, [])
    c.o = [c.o[0], *c.c[:-1]]
    c.h = [max(h, o) for h, o in zip(c.h, c.o, strict=True)]
    c.lo = [min(lo, o) for lo, o in zip(c.lo, c.o, strict=True)]
    return c


def test_grid_count_unique_and_no_exit_optimization() -> None:
    grid = roles.configs()
    assert len(grid) == len({c.key for c in grid}) == 3840
    assert all(
        pilot.ledger_config(c).exit == "48H" and pilot.ledger_config(c).stop_pct == "0.03"
        for c in grid
    )
    assert roles.RoleConfig("CCI", "R0", "NONE", "NONE", "NONE") in grid


def test_invalid_role_rejected() -> None:
    with pytest.raises(ValueError):
        roles.RoleConfig("RSI21", "R0", "NONE", "NONE", "NONE")


@pytest.mark.parametrize("close,expected", [(2.0, 1.0), (0.0, -1.0), (1.0, 0.0)])
def test_cmf_hand_calculated(close: float, expected: float) -> None:
    out = roles.cmf([2.0] * 21, [0.0] * 21, [close] * 21, [10.0] * 21)
    assert math.isnan(out[18]) and out[19:] == [expected] * 2


def test_cmf_zero_range_zero_volume_and_length_error() -> None:
    assert roles.cmf([1.0] * 20, [1.0] * 20, [1.0] * 20, [2.0] * 20)[-1] == 0
    assert math.isnan(roles.cmf([2.0] * 20, [0.0] * 20, [1.0] * 20, [0.0] * 20)[-1])
    with pytest.raises(ValueError):
        roles.cmf([1.0], [], [1.0], [1.0])


def pivot_input(
    second_low: float, second_rsi: float
) -> tuple[list[float], list[float], list[float]]:
    high = [100.0 + i for i in range(30)]  # no high extrema or ties
    low = [30.0 + i for i in range(30)]
    low[3], low[9] = 10, second_low
    rsi = [50.0] * 30
    rsi[3], rsi[9] = 20, second_rsi
    return high, low, rsi


def test_regular_bull_only_at_confirmation_then_expires() -> None:
    result = roles.confirmed_pivots(*pivot_input(9, 30))
    assert not any(result.regular_bull[:11])
    assert result.regular_bull[11:23] == [True] * 12
    assert not result.regular_bull[23]
    assert result.last_low[10] == 10 and result.last_low[11] == 9
    assert result.known[11] and (9, 11, "regular_bull") in result.events


def test_hidden_bull_opposite_signs_and_no_false_regular() -> None:
    result = roles.confirmed_pivots(*pivot_input(11, 10))
    assert result.hidden_bull[11] and not result.regular_bull[11]


def test_bear_divergences_are_annotations_not_bull_veto() -> None:
    high, low, rs = pivot_input(9, 30)
    # Reflect price and RSI to obtain increasing highs and weaker RSI highs.
    out = roles.confirmed_pivots(
        [200 - p for p in low], [0 - p for p in high], [100 - x for x in rs]
    )
    assert out.regular_bear[11] and not out.regular_bull[11]


def test_tie_and_simultaneous_pivots_reset_evidence() -> None:
    high, low, rs = pivot_input(9, 30)
    low[10] = low[9]
    result = roles.confirmed_pivots(high, low, rs)
    assert not result.known[11] and math.isnan(result.last_low[11])
    assert (9, 11, "UNKNOWN") in result.events
    high, low, rs = pivot_input(9, 30)
    high[9] = 500
    result = roles.confirmed_pivots(high, low, rs)
    assert (9, 11, "UNKNOWN") in result.events and not result.regular_bull[11]


def test_nan_oscillator_pair_is_unknown_and_bad_length_rejected() -> None:
    h, lo, rs = pivot_input(9, 30)
    rs[3] = float("nan")
    assert not roles.confirmed_pivots(h, lo, rs).known[11]
    with pytest.raises(ValueError):
        roles.confirmed_pivots(h, [], rs)


@pytest.mark.leakage
def test_pivots_prefix_does_not_use_future_right_bars_early() -> None:
    args = pivot_input(9, 30)
    full = roles.confirmed_pivots(*args)
    for end in range(5, 30):
        prefix = roles.confirmed_pivots(*(a[:end] for a in args))
        assert prefix.known == full.known[:end]
        assert prefix.regular_bull == full.regular_bull[:end]
        assert prefix.events == tuple(e for e in full.events if e[1] < end)


@pytest.mark.leakage
def test_all_feature_masks_and_daily_backdrops_are_future_independent() -> None:
    c = coin(1400)
    full = roles.masks(c)
    prefix_bars = list(
        zip(c.t[:900], c.o[:900], c.h[:900], c.lo[:900], c.c[:900], c.v[:900], strict=True)
    )
    prefix_coin = build_coin(c.name, prefix_bars, [])
    prefix = roles.masks(prefix_coin)
    for key in full.triggers:
        assert prefix.triggers[key] == [i for i in full.triggers[key] if i < 900]
    for field in ("confirms", "participations", "risks"):
        for key, values in getattr(prefix, field).items():
            assert values == getattr(full, field)[key][:900]
    for key in roles.BACKDROPS:
        assert prefix_coin.regime[key] == c.regime[key][:900]


def test_rvol_uses_previous_window_not_self_in_mean() -> None:
    c = coin()
    c.v = [100.0] * len(c.t)
    c.v[30] = 150
    data = roles.masks(c)
    assert data.participations["RVOL"][30]
    assert not data.participations["RVOL"][29]


def test_unfiltered_mapping_and_reference_signals_match_previous_engine() -> None:
    c = coin()
    engine = pilot.Engine([c])
    cfg = roles.RoleConfig("RSI14_60", "R0", "NONE", "NONE", "NONE")
    ref = base.Config("RSI", 14, 60, 0, 0, "R0", "48H", "0.03")
    assert engine.signals(c, pilot.ledger_config(cfg)) == base.Engine([c]).signals(c, ref)
    assert engine.signals(c, ref) == base.Engine([c]).signals(c, ref)
    filtered = replace(cfg, confirm="EMA50")
    assert set(engine.signals(c, pilot.ledger_config(filtered))) <= set(engine.signals(c, ref))


def test_unknown_only_blocks_requested_divergence_role() -> None:
    c = coin()
    data = roles.masks(c)
    cfg = roles.RoleConfig("RSI7_70", "R0", "NONE", "NONE", "NONE")
    c.regime["R0"][25] = True
    data.pivots.known[25] = False
    data.risks["REGULAR"][25] = False
    assert roles.admitted(c, data, cfg, 25)
    assert not roles.admitted(c, data, replace(cfg, risk="REGULAR"), 25)


def test_selection_ignores_check_and_tail_and_honors_eligibility() -> None:
    def row(key: str, t: float, n: int, coin_net: float) -> dict[str, object]:
        return {
            "key": key,
            "weekly_hac_t": t,
            "trades": n,
            "unresolved": 0,
            "symbols": {s: {"trades": n // 2, "mean_bps": coin_net} for s in base.SYMBOLS},
        }

    rows = [row("A", 2, 100, 1), row("B", 5, 3, 9), row("C", 3, 100, -1)]
    assert pilot.nominate(rows)["key"] == "A"
    assert pilot.nominate(rows)["train_eligible"]
    assert not pilot.nominate([rows[-1]])["train_eligible"]


@pytest.mark.parametrize("cost,delay", [(1, 0), (2, 0), (1, 1)])
def test_account_adapter_matches_frozen_ledger_and_reconciles(
    monkeypatch: pytest.MonkeyPatch,
    cost: int,
    delay: int,
) -> None:
    c = coin(420)
    c.v = [100000.0] * len(c.t)
    c.fund_t = c.t[2::2]
    c.fund_r = [0.0001 if i % 2 else -0.0002 for i in range(len(c.fund_t))]
    cfg = base.Config("RSI", 14, 60, 0, 0, "R0", "48H", "0.03")
    monkeypatch.setattr(base, "START", 24 * H4)
    monkeypatch.setattr(base, "END", 414 * H4)
    engine = base.Engine([c])
    old = base.account(engine, cfg, cost, delay)
    new = pilot.account(engine, cfg, base.START, base.END, cost, delay)
    for key, value in old.items():
        if key == "accounting_residual_usdt":
            assert abs(value - new[key]) < 1e-18
        else:
            assert value == new[key], key
    assert new["accounting_residual_usdt"] < 1e-18
    assert not new["open_terminal"]
    smaller = pilot.account(
        engine, cfg, base.START, base.END, cost, delay, size_scale=Decimal("0.5")
    )
    assert smaller["mean_exposure"] < new["mean_exposure"]


def test_account_rejects_unregistered_inputs() -> None:
    c = coin()
    engine = base.Engine([c])
    cfg = base.Config("RSI", 14, 60, 0, 0, "R0", "48H", "0.03")
    with pytest.raises(ValueError):
        pilot.account(engine, cfg, base.START, base.END + 1)
    with pytest.raises(ValueError):
        pilot.account(engine, cfg, base.START, base.END, size_scale=Decimal("1.1"))


def test_excursions_exclude_final_bar_unknown_order() -> None:
    c = coin()
    engine = base.Engine([c])
    cfg = base.Config("RSI", 14, 60, 0, 0, "R0", "48H", "0.03")
    ep = engine.episode(c, 30, cfg, 90 * H4)
    assert ep is not None
    before = pilot.excursions(engine, [ep])
    c.h[ep.exit_i], c.lo[ep.exit_i] = 10000, 0.01
    assert pilot.excursions(engine, [ep]) == before


def test_bonferroni_records_expanded_search_family() -> None:
    assert pilot.TRIALS == 13074
    assert pilot.summary([], base.START, base.END)["bonferroni_normal_p"] == 1
