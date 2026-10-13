"""Synthetic source availability, reopening, metrics, and saved accounting."""

from __future__ import annotations

import json
import zipfile
from array import array
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tests.research.test_wallet_grid_20261013 import account, bar, block, minute, request, short

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import wallet_grid_20261013 as p
from scripts import run_wallet_grid_20261013 as runner
from scripts import wallet_grid_data_20261013 as data

D = Decimal


def archive(path: Path, records: list[tuple[int, str]]) -> None:
    rows = [
        f"{at},{value},{value},{value},{value},100000,{at + 59999},100000,100,0,0,0"
        for at, value in records
    ]
    with zipfile.ZipFile(path, "x") as z:
        z.writestr("source.csv", "\n".join(rows))


def test_raw_missing_is_none_and_valuation_uses_past_only(tmp_path: Path):
    lo, end = data.CLOSED[0]
    path = tmp_path / "BTCUSDT-1m-2020-06.zip"
    archive(path, [(lo - p.MINUTE, "100"), (lo, "999"), (end, "105"), (end + p.MINUTE, "106")])
    cols, state = data.align_source(path, lo - p.MINUTE, end + 2 * p.MINUTE, spot=True)
    series = data.Series(lo - p.MINUTE, cols, state, bar())
    assert series.raw(2) is None
    assert state[1] == 2  # Existing boundary row is retained but closed.
    assert series.bar(1).o == 100 and not series.bar(1).observed
    assert series.bar(2).o == 100 and series.bar(2).volume == 0
    assert series.bar(len(state) - 2).o == 105
    assert series.bar(len(state) - 3).volume == 0


@pytest.mark.leakage
def test_future_reopening_price_does_not_change_closed_valuation(tmp_path: Path):
    lo, end = data.CLOSED[0]
    values = []
    for n, price in enumerate(("105", "10000")):
        path = tmp_path / f"BTCUSDT-{n}-1m-2020-06.zip"
        archive(path, [(lo - p.MINUTE, "100"), (end, price)])
        cols, state = data.align_source(path, lo - p.MINUTE, end + p.MINUTE, spot=True)
        series = data.Series(lo - p.MINUTE, cols, state, bar())
        values.append([series.bar(i) for i in range(1, len(state) - 1)])
    assert values[0] == values[1]


def test_unknown_gap_is_rejected_not_filled(tmp_path: Path):
    path = tmp_path / "BTCUSDT-1m-2020-06.zip"
    archive(path, [(data.START, "100")])
    with pytest.raises(DataIntegrityError, match="unexplained"):
        data.align_source(path, data.START, data.START + 2 * p.MINUTE, spot=True)


def test_futures_gap_is_not_treated_as_spot_closure(tmp_path: Path):
    lo, end = data.CLOSED[0]
    path = tmp_path / "BTCUSDT-1m-2020-06.zip"
    archive(path, [(lo - p.MINUTE, "100"), (end, "105")])
    with pytest.raises(DataIntegrityError, match="unexplained"):
        data.align_source(path, lo - p.MINUTE, end + p.MINUTE, spot=False)


@pytest.mark.leakage
def test_protected_month_rejected_before_read():
    with pytest.raises(DataIntegrityError, match="protected"):
        data.month_bounds("2026-09")


def test_closed_spot_has_no_fill_but_futures_close_works():
    a = account()
    short(a)
    request(a)
    a.orders[0, "CLOSE"] = p.Order("CLOSE", D(100), D(1), 0, p.H4, True)
    a.orders[0, "SPOT"] = p.Order("BUY", D(100), D(1), 0, p.H4, True)
    m = minute()
    m = replace(m, spot=(replace(m.spot[0], observed=False, volume=D(0)), m.spot[1]))
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert not a.shorts
    assert a.spot_qty[0] == 0
    assert a.orders[0, "SPOT"].attempts == 0
    assert a.fills[0]["leg"] == "LONG_PRIORITY"
    assert a.returns[0].reasons["VENUE_CLOSED"] == 1


def test_stale_portfolio_prevents_new_futures_exposure():
    a = account()
    a.last_rates[0] = D(0)
    a.orders[0, "OPEN"] = p.Order("OPEN", D(100), D(0), 0, p.H4)
    m = minute()
    m = replace(m, spot=(replace(m.spot[0], observed=False), m.spot[1]))
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert not a.shorts
    assert a.orders[0, "OPEN"].attempts == 0
    assert a.counts["stale_new_exposure_veto"] == 1


def test_reopening_first_minute_has_no_previous_liquidity():
    a = account()
    a.goals[0] = D("0.4")
    a.orders[0, "SPOT"] = p.Order("BUY", D(100), D(1), 0, p.H4)
    m = minute()
    m = replace(
        m,
        previous_spot=(
            replace(m.previous_spot[0], observed=False, volume=D(0)),
            m.previous_spot[1],
        ),
    )
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert not a.fills
    p._minute(a, minute(p.MINUTE), ((), ()), 20 * p.DAY)
    assert len(a.fills) == 1


def test_minimum_spot_dust_is_not_marked_flat():
    a = account()
    a.spot_qty[0] = D("0.01")
    a.orders[0, "SPOT"] = p.Order("SELL", D(100), D("0.01"), 0, p.H4)
    p._minute(a, minute(), ((), ()), 20 * p.DAY)
    assert not a.fills and a.spot_qty[0] == D("0.01")
    b = block(signals=(True, False))
    p._begin(a, b)
    assert a.counts["short_signal_conflict"] == 1


def test_pending_return_keeps_wall_clock_expiry_when_closed():
    a = account()
    request(a)
    m = minute(300 * p.MINUTE)
    m = replace(m, spot=(replace(m.spot[0], observed=False), m.spot[1]))
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert a.returns[0].window_expired and not a.fills


def test_block_open_does_not_use_later_reopening_price():
    # 4h opening unavailable, then future price 1000. Opening must remain past 100.
    cols = data.tuple_arrays(240)
    state = array("b", [0]) * 240
    for i in range(120, 240):
        for j in range(4):
            cols[j][i] = 1000 * data.SCALE
        cols[4][i] = data.SCALE
        state[i] = 1
    series = data.Series(data.START, cols, state, bar())
    aggregate = series.aggregate(0)
    assert aggregate.o == 100 and aggregate.c == 1000 and not aggregate.observed
    assert aggregate.volume == 120


def test_saved_fill_reconciliation_and_coin_contributions():
    case = p.cases()[0]
    r = p.simulate([block(targets=(D("0.4"), D("0.4")))], end_ms=p.H4, selected=(case,))[case.key]
    saved = json.loads(json.dumps(r, default=str))
    assert runner.reconcile_ledger(saved)["saved_fill_reconciliation"] == "MATCH"
    assert sum(r["coin_net_before_reserve"].values()) == r["terminal_equity"] - p.INITIAL
    saved["fills"][0]["fee"] = "100"
    with pytest.raises(DataIntegrityError, match="mismatch"):
        runner.reconcile_ledger(saved)


def test_saved_futures_roundtrip_reconciliation():
    case = p.Case(D("0.85"), "PRIORITY_1M", p.SCENARIOS[0])
    frames = [block(i * p.H4) for i in range(48)]
    frames[0] = replace(
        frames[0], signals=(True, False), funding=((p.Funding(0, D("0.001"), D(100), True),), ())
    )
    r = p.simulate(frames, end_ms=8 * p.DAY, selected=(case,))[case.key]
    saved = json.loads(json.dumps(r, default=str))
    assert r["trades"]
    assert runner.reconcile_ledger(saved)["saved_fill_reconciliation"] == "MATCH"


def test_one_shot_marker_blocks_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "OUT", tmp_path)
    runner.save("run_started.json", {"kept": True})
    with pytest.raises(DataIntegrityError, match="no rerun"):
        runner.pilot()
    assert json.loads((tmp_path / "run_started.json").read_text()) == {"kept": True}


def test_month_year_attribution_and_terminal_reserve_once():
    run: dict[str, Any] = {
        "trace": [],
        "trades": [],
        "fills": [],
        "returns": [],
        "funding_events": [],
        "net": D(4990),
        "terminal_cost_reserve": D(10),
        "coin_reserve": {"BTCUSDT": D(10), "ETHUSDT": D(0)},
        "turnover": D(0),
    }
    for at, eq in (
        (runner.epoch_year(2024), D(11000)),
        (runner.epoch_year(2026), D(13000)),
        (data.END, D(15000)),
    ):
        run["trace"].append(
            {
                "at_ms": at,
                "equity": eq,
                "coin_net": {"BTCUSDT": eq - p.INITIAL, "ETHUSDT": D(0)},
                "spot_cash": D(8500),
                "futures_cash": D(1500),
                "halted": False,
                "spot_close_observed": (True, True),
            }
        )
    result = runner.metrics(run)
    assert sum(x["pnl"] for x in result["annual"].values()) == 4990
    assert sum(x["pnl"] for x in result["monthly"].values()) == 4990
    assert sum(x["pnl"] for x in result["periods"].values()) == 4990
    assert "2026-09" not in result["monthly"]
