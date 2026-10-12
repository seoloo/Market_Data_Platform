"""Synthetic accounting diagnostics; no real prices/performance or operating I/O."""

from __future__ import annotations

import math
import sys

import pytest

from auto_trading.backtest.overlay import OverlayBar, OverlayConfig
from auto_trading.research.short_search_229 import H4, build_coin
from scripts import audit_h1_o_ag_20261012 as audit


def test_overlap_and_mark_to_market_counterexamples() -> None:
    r = audit.synthetic()
    assert r["overlap_sequential"] == pytest.approx(99)
    assert r["overlap_fixed_cash"] == 100
    assert r["hidden_short_mdd"] > r["exit_only_mdd"]
    assert r["funding_long"] == pytest.approx(-r["funding_short"])
    assert r["sorted_first_of3_mean_index"] < 300 < r["uniform_expected_index"]


def test_incomplete_and_noncontiguous_four_hour_bars_rejected() -> None:
    rows = [(i * 3_600_000, 100, 102, 98, 101, 10) for i in (0, 1, 2)]
    assert not audit.aggregate(rows)
    rows.append((3 * 3_600_000, 100, 103, 97, 102, 10))
    got = audit.aggregate(rows)
    assert got[0].c == 102 and got[0].v == 40
    assert got[0].low == 97 and got[0].h == 103


@pytest.mark.leakage
def test_h1_indicators_do_not_recount_the_past() -> None:
    bars = [
        (
            i * H4,
            100 + math.sin(i),
            102 + math.sin(i),
            98 + math.sin(i),
            100 + math.sin(i),
            1000.0,
        )
        for i in range(500)
    ]
    a = build_coin("BTCUSDT", bars[:350], [])
    b = build_coin("BTCUSDT", bars, [])
    assert a.regime["R5"] == b.regime["R5"][:350]
    assert a.trig["T2"] == b.trig["T2"][:350]


def data_fixture(monkeypatch: pytest.MonkeyPatch, rise: float = 0) -> audit.Inputs:
    monkeypatch.setattr(audit, "FROM_MS", audit.DAY)
    monkeypatch.setattr(audit, "END_MS", 3 * audit.DAY)
    symbols = audit.SYMBOLS
    four = {
        s: {
            t: audit.Bar(
                t,
                100 + rise * t / audit.DAY,
                101 + rise * t / audit.DAY,
                99 + rise * t / audit.DAY,
                100 + rise * t / audit.DAY,
                1e9,
            )
            for t in range(audit.DAY - H4, 3 * audit.DAY, H4)
        }
        for s in symbols
    }
    daily = {
        s: {d: OverlayBar(99, 100) for d in range(0, 3 * audit.DAY, audit.DAY)} for s in symbols
    }
    return audit.Inputs(daily, daily, four, four, {s: [] for s in symbols}, [], {})


def test_constant_prices_preserve_cash_before_and_after_fees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = data_fixture(monkeypatch)
    cfg = OverlayConfig()
    empty = {2 * audit.DAY: dict.fromkeys(audit.SYMBOLS, (0.0, 0.0))}
    r = audit.replay(data, empty, [], cfg, "BASE")
    assert r["final"] == audit.INITIAL
    plan = {2 * audit.DAY: {"BTCUSDT": (10.0, 10.0), "ETHUSDT": (0.0, 0.0)}}
    r = audit.replay(data, plan, [], cfg, "BASE")
    assert r["net"] == pytest.approx(-2 * 1000 * (cfg.trend.spot_cost + cfg.trend.perp_cost))
    assert r["costs"] == pytest.approx(-r["net"])
    assert r["accounting_residual"] == pytest.approx(0, abs=1e-8)


def test_short_loss_is_marked_before_exit_and_risk_clipped(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch, rise=0.5)
    e = audit.Episode("BTCUSDT", audit.DAY, 3 * audit.DAY, 101, False)
    r = audit.replay(data, {}, [e], OverlayConfig(), "SWITCH")
    assert r["net"] < 0
    assert r["mdd"] > 0
    assert r["counters"]["short_entries"] == 1
    assert r["counters"]["risk_clipped"] == 1
    assert r["max_gross_exposure"] < 0.5
    assert r["accounting_residual"] == pytest.approx(0, abs=1e-8)


def test_limit_gap_is_not_optimistic_full_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch)
    first = data.perp4["BTCUSDT"][audit.DAY]
    data.perp4["BTCUSDT"][audit.DAY] = audit.Bar(first.at_ms, 90, 91, 89, 90, first.v)
    e = audit.Episode("BTCUSDT", audit.DAY, 3 * audit.DAY, 90, False)
    r = audit.replay(data, {}, [e], OverlayConfig(), "SWITCH")
    assert r["counters"]["limit_miss"] >= 1
    assert r["counters"].get("short_entries", 0) == 0


def test_same_asset_cannot_keep_long_and_short_futures(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch)
    entry = 2 * audit.DAY
    plan = {audit.DAY + H4: {"BTCUSDT": (0.0, 10.0), "ETHUSDT": (0.0, 0.0)}}
    e = audit.Episode("BTCUSDT", entry, 3 * audit.DAY, 100, False)
    r = audit.replay(data, plan, [e], OverlayConfig(), "SWITCH")
    assert r["counters"]["short_entries"] == 1
    assert r["counters"]["futures_fills"] >= 4
    assert r["max_gross_exposure"] < 0.5


def test_actual_held_short_quantity_receives_funding(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch)
    data.funding["BTCUSDT"] = [(audit.DAY + 1, 0.001)]
    e = audit.Episode("BTCUSDT", audit.DAY, 3 * audit.DAY, 100, False)
    r = audit.replay(data, {}, [e], OverlayConfig(), "SWITCH")
    assert r["funding"] > 0
    assert r["accounting_residual"] == pytest.approx(0, abs=1e-8)


def test_zero_prior_volume_does_not_fully_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch)
    t = audit.DAY - H4
    data.perp4["BTCUSDT"][t] = audit.Bar(t, 100, 101, 99, 100, 0)
    e = audit.Episode("BTCUSDT", audit.DAY, 3 * audit.DAY, 100, False)
    r = audit.replay(data, {}, [e], OverlayConfig(), "SWITCH")
    assert r["counters"]["liquidity_partial"] == 1
    assert r["counters"].get("short_entries", 0) == 0
    assert r["final"] == audit.INITIAL


def test_fees_are_not_applied_again_to_exit_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch)
    plan = {audit.DAY + H4: {"BTCUSDT": (10.0, 10.0), "ETHUSDT": (0.0, 0.0)}}
    one = audit.replay(data, plan, [], OverlayConfig(), "BASE")
    two = audit.replay(data, plan, [], OverlayConfig(), "BASE", fee_mult=2)
    assert two["costs"] == pytest.approx(2 * one["costs"])
    assert two["net"] == pytest.approx(2 * one["net"])


def test_ag_initial_capital_halt_cannot_reenter(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch)
    for s in audit.SYMBOLS:
        for t in range(2 * audit.DAY, 3 * audit.DAY, H4):
            data.spot4[s][t] = audit.Bar(t, 25, 26, 24, 25, 1e9)
    qty = {"BTCUSDT": (100.0, 0.0), "ETHUSDT": (0.0, 0.0)}
    plan = {audit.DAY + H4: qty, 3 * audit.DAY: qty}
    cfg = OverlayConfig(exposure=3.0, catastrophe_stop=0.2, halt_drawdown=1.0)
    r = audit.replay(data, plan, [], cfg, "BASE")
    assert r["final"] < audit.INITIAL * 0.3
    assert r["counters"]["initial_capital_halt"] == 1
    assert r["accounting_residual"] == pytest.approx(0, abs=1e-8)


def test_pilot_rejects_changed_inputs_before_performance(monkeypatch: pytest.MonkeyPatch) -> None:
    data = data_fixture(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["audit", "pilot"])
    monkeypatch.setattr(audit, "git", lambda *args: "")
    monkeypatch.setattr(audit, "load", lambda: data)
    with pytest.raises(ValueError, match="inputs differ"):
        audit.main()
