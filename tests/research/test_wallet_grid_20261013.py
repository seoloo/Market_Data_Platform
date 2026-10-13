"""Synthetic-only wallet/grid checks; no historical strategy performance."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research import wallet_grid_20261013 as p
from scripts import run_wallet_grid_20261013 as runner

D = Decimal


def bar(price: str = "100", volume: str = "100000") -> p.Bar:
    return p.Bar(D(price), D(price), D(price), D(price), D(volume))


def minute(at: int = 0, *, volume: str = "100000", price: str = "100") -> p.Minute:
    pair = (bar(price, volume), bar(price, volume))
    return p.Minute(at, pair, pair, pair, pair, at)


def account(mode: p.Mode = "PRIORITY_1M", scenario: p.Scenario | None = None) -> p.Account:
    return p.Account(p.Case(D("0.85"), mode, scenario or p.SCENARIOS[0]))


def short(a: p.Account, i: int = 0, qty: str = "1", entry_ms: int = 0) -> None:
    a.shorts[i] = p.Short(D(qty), D(100), D(100), entry_ms, D(qty))


def request(a: p.Account, i: int = 0, *, at: int = 0) -> None:
    a.goals[i] = D("0.5")
    a.returns[i] = p.Return(at, D(100), at)
    a.last_closed_ack[i] = at


@dataclass
class FixedMinutes:
    at_ms: int
    price: str = "100"
    previous_offset_ms: int = 0

    def get(self, index: int) -> p.Minute:
        m = minute(self.at_ms + index * p.MINUTE, price=self.price)
        return replace(m, previous_as_of_ms=m.at_ms + self.previous_offset_ms)


def block(
    at: int = 0,
    *,
    targets: tuple[Decimal, Decimal] | None = None,
    signals: tuple[bool, bool] = (False, False),
    price: str = "100",
) -> p.Block:
    pair = (bar(price), bar(price))
    return p.Block(
        at, pair, pair, pair, pair, targets, at, signals, at, ((), ()), FixedMinutes(at, price)
    )


def tick(a: p.Account, at: int, *, volume: str = "100000", price: str = "100") -> None:
    p._minute(a, minute(at, volume=volume, price=price), ((), ()), p.DAY * 20)


def test_registered_cases_and_wallets():
    assert len(p.cases()) == len({c.key for c in p.cases()}) == 36
    for c in p.cases():
        a = p.Account(c)
        assert a.spot_cash == p.INITIAL * c.spot_ratio
        assert a.futures_cash == p.INITIAL - a.spot_cash
        assert a.spot_cash + a.futures_cash == p.INITIAL


def test_return_attempt_budget_is_not_reset_by_recreation():
    a = account()
    request(a)
    for n in range(60, 80):
        tick(a, n * p.MINUTE, price="101")
    assert a.returns[0].buy_attempts == 3
    assert a.counts["return_buy_attempts"] == 3
    assert a.counts["return_attempt_exhausted"] == 1
    assert a.counts["spot_buy_cap_miss"] == 3
    assert not a.fills
    assert (0, "SPOT") not in a.orders


def test_partial_return_has_only_three_pieces():
    a = account()
    request(a)
    for n in range(60, 80):
        tick(a, n * p.MINUTE, volume="1000")
    assert a.returns[0].buy_attempts == 3
    assert a.returns[0].bought == D(3)
    assert len(a.fills) == 3
    assert a.counts["return_attempt_exhausted"] == 1


def test_new_daily_goal_version_resets_child_not_root():
    a = account()
    request(a)
    for n in range(60, 64):
        tick(a, n * p.MINUTE, price="101")
    p._begin(a, block(p.DAY, targets=(D("0.4"), D(0))))
    r = a.returns[0]
    assert r.requested_ms == 0
    assert r.goal_version_ms == p.DAY
    assert r.buy_attempts == 0
    assert r.versions == [{"goal_version_ms": 0, "attempts": 3, "window_expired": False}]
    tick(a, p.DAY, volume="1000")
    assert r.buy_attempts == 1
    assert a.counts["return_requests"] == 0  # helper inserted one root, no second root


def test_goal_zero_cancels_return_and_pending_buy():
    a = account()
    request(a)
    tick(a, 60 * p.MINUTE, volume="1000")
    p._begin(a, block(p.DAY, targets=(D(0), D(0))))
    assert not a.returns
    assert a.orders[0, "SPOT"].kind == "SELL"
    assert a.return_events[0]["status"] == "GOAL_ZERO"


def test_sixty_minutes_starts_after_flat_ack():
    a = account()
    short(a)
    request(a)
    a.goals[0] = D("0.4")
    a.orders[0, "CLOSE"] = p.Order("CLOSE", D(100), D(1), 0, p.H4, True)
    tick(a, 0)
    assert not a.shorts
    assert a.last_closed_ack[0] == p.MINUTE
    tick(a, 60 * p.MINUTE)
    assert not any(f["leg"] == "BUY_SPOT" for f in a.fills)
    tick(a, 61 * p.MINUTE)
    assert a.returns.get(0) is None
    assert a.return_events[0]["first_ms"] == 61 * p.MINUTE


def test_ack5_changes_confirmation_not_cooldown_rule():
    a = account(scenario=p.SCENARIOS[3])
    short(a)
    request(a)
    a.orders[0, "CLOSE"] = p.Order("CLOSE", D(100), D(1), 0, p.H4, True)
    tick(a, 0)
    assert a.last_closed_ack[0] == 5 * p.MINUTE
    tick(a, 64 * p.MINUTE)
    assert len(a.fills) == 1
    tick(a, 65 * p.MINUTE)
    assert len(a.fills) == 2


def test_return_grid_is_only_return_execution_difference():
    slow, fast = account("PRIORITY_4H"), account()
    for a in (slow, fast):
        request(a)
        a.goals[0] = D("0.4")
        for n in range(60, 241):
            tick(a, n * p.MINUTE)
    assert fast.return_events[0]["first_ms"] == 60 * p.MINUTE
    assert slow.return_events[0]["first_ms"] == 240 * p.MINUTE
    assert fast.case.scenario == slow.case.scenario


def test_window_expires_without_extension():
    a = account()
    request(a)
    tick(a, 300 * p.MINUTE)
    tick(a, 301 * p.MINUTE)
    assert a.returns[0].window_expired
    assert a.counts["return_window_expired"] == 1
    assert not a.fills


def test_common_busy_ack_blocks_second_coin():
    a = account(scenario=p.SCENARIOS[3])
    a.goals = [D("0.4"), D("0.4")]
    for i in range(2):
        a.orders[i, "SPOT"] = p.Order("BUY", D(100), D(1), 0, p.H4)
    tick(a, 0)
    assert [f["symbol"] for f in a.fills] == ["BTCUSDT"]
    tick(a, 4 * p.MINUTE)
    assert len(a.fills) == 1
    tick(a, 5 * p.MINUTE)
    assert len(a.fills) == 2


def test_short_and_spot_same_coin_cannot_overlap():
    a = account()
    short(a)
    a.goals[0] = D("0.5")
    a.orders[0, "SPOT"] = p.Order("BUY", D(100), D(5), 0, p.H4)
    tick(a, 0)
    assert a.spot_qty[0] == 0
    assert a.counts["spot_buy_risk_or_short_veto"] == 1
    assert a.orders[0, "SPOT"].attempts == 0


def test_risk_veto_does_not_spend_dispatch_budget():
    a = account()
    a.goals[0] = D("0.5")
    a.cooldown_until_ms = p.H4
    a.orders[0, "SPOT"] = p.Order("BUY", D(100), D(5), 0, p.H4)
    tick(a, 0)
    assert a.orders[0, "SPOT"].attempts == 0
    assert not a.fills


def test_risk_halt_does_not_block_short_reduction():
    a = account()
    short(a)
    a.halted = True
    a.orders[0, "CLOSE"] = p.Order("CLOSE", D(100), D(1), 0, p.H4)
    tick(a, 0)
    assert not a.shorts
    assert a.fills[0]["leg"] == "48H"


def test_short_size_uses_margin_cap_and_no_wallet_transfer():
    a = account()
    original_spot = a.spot_cash
    a.last_rates[0] = D(0)
    a.orders[0, "OPEN"] = p.Order("OPEN", D(100), D(0), 0, p.H4)
    tick(a, 0)
    pos = a.shorts[0]
    eq = a.equity(minute().spot, minute().perp, "o")
    assert pos.qty * pos.entry <= eq * D("0.1")
    assert a.futures_cash >= D("1.5") * pos.qty * pos.entry
    assert a.spot_cash == original_spot


def test_short_unknown_funding_fails_closed():
    a = account()
    a.orders[0, "OPEN"] = p.Order("OPEN", D(100), D(0), 0, p.H4)
    tick(a, 0)
    assert not a.shorts
    assert a.orders[0, "OPEN"].attempts == 0
    assert a.counts["funding_unknown_veto"] == 1


def test_short_entry_first_partial_cancels_remainder():
    a = account()
    a.last_rates[0] = D(0)
    a.orders[0, "OPEN"] = p.Order("OPEN", D(100), D(0), 0, p.H4)
    for n in range(4):
        tick(a, n * p.MINUTE, volume="1000")
    assert a.shorts[0].qty == 1
    assert a.counts["short_opened"] == 1
    assert len(a.fills) == 1


def test_zero_current_volume_has_no_ordinary_fill():
    a = account()
    a.goals[0] = D("0.5")
    a.orders[0, "SPOT"] = p.Order("BUY", D(100), D(1), 0, p.H4)
    m = minute()
    m = replace(m, spot=(bar(volume="0"), m.spot[1]))
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert not a.fills
    assert a.orders[0, "SPOT"].attempts == 1


def test_price_touch_does_not_fill_above_open_cap():
    a = account()
    a.goals[0] = D("0.5")
    a.orders[0, "SPOT"] = p.Order("BUY", D(100), D(1), 0, p.H4)
    m = minute(price="101")
    m = replace(m, spot=(replace(m.spot[0], low=D(99)), m.spot[1]))
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert not a.fills
    assert a.counts["spot_buy_cap_miss"] == 1


def test_stop_on_new_entry_same_minute_is_charged():
    a = account()
    a.last_rates[0] = D(0)
    a.orders[0, "OPEN"] = p.Order("OPEN", D(100), D(0), 0, p.H4)
    m = minute()
    m = replace(m, perp=(replace(m.perp[0], h=D(105)), m.perp[1]))
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert not a.shorts
    assert a.trades[0]["reason"] == "STOP"
    assert a.trades[0]["net"] < 0
    assert a.last_closed_ack[0] == 2 * p.MINUTE
    assert a.dd_bound > 0


def test_two_stops_then_offset_funding_is_attributed_per_coin():
    a = account()
    for i in range(2):
        short(a, i)
    m = minute()
    m = replace(m, perp=(replace(m.perp[0], h=D(104)), replace(m.perp[1], h=D(104))))
    event = p.Funding(1, D("-0.01"), D(100), False)
    p._minute(a, m, ((event,), (event,)), 20 * p.DAY)
    assert len(a.trades) == 2
    assert [t["funding"] for t in a.trades] == [D(-1), D(-1)]
    assert a.funding == -2
    assert a.counts["funding_proxy_applied"] == 2


def test_positive_offset_funding_is_omitted_after_stop():
    a = account()
    short(a)
    m = minute()
    m = replace(m, perp=(replace(m.perp[0], h=D(104)), m.perp[1]))
    event = p.Funding(1, D("0.01"), D(100), True)
    p._minute(a, m, ((event,), ()), 20 * p.DAY)
    assert a.funding == 0
    assert a.trades[0]["funding"] == 0


def test_exact_funding_precedes_ordinary_close():
    a = account()
    short(a)
    a.orders[0, "CLOSE"] = p.Order("CLOSE", D(100), D(1), 0, p.H4)
    event = p.Funding(0, D("0.01"), D(100), True)
    p._minute(a, minute(), ((event,), ()), 20 * p.DAY)
    assert a.funding == 1
    assert a.trades[0]["funding"] == 1
    # 100 * 1.00045 = 100.045; fee=0.0500225; price loss=0.045.
    assert a.trades[0]["net"] == D("0.9049775")


def test_four_losses_extend_common_cooldown():
    a = account()
    for n in range(4):
        short(a, entry_ms=n * p.DAY)
        m = minute(n * p.DAY)
        m = replace(m, perp=(replace(m.perp[0], h=D(104)), m.perp[1]))
        p._minute(a, m, ((), ()), 20 * p.DAY)
    assert a.counts["four_loss_cooldown"] == 1
    assert a.cooldown_until_ms == 3 * p.DAY + (2 + 240) * p.MINUTE


@given(st.integers(min_value=0, max_value=850000))
def test_cash_and_coin_cap_property(cents: int):
    a = account()
    a.spot_cash = D(cents) / 100
    original_futures = a.futures_cash
    a.goals[0] = D("0.5")
    a.orders[0, "SPOT"] = p.Order("BUY", D(100), D(1000), 0, p.H4)
    tick(a, 0)
    e = a.equity(minute().spot, minute().perp, "o")
    assert a.spot_cash >= 0
    assert a.futures_cash == original_futures
    assert a.spot_qty[0] * 100 <= D("0.5") * e


def test_full_replay_wallet_decomposition_and_determinism():
    selected = (p.cases()[0],)
    b = block(targets=(D("0.4"), D("0.4")))
    result = p.simulate([b], end_ms=p.H4, selected=selected)
    r = result[selected[0].key]
    assert r["residual"] == 0
    assert r["funding"] == 0
    assert r["net"] < 0
    assert r["net"] == r["spot_gross"] - r["spot_fee"] - r["spot_slip"] - r["terminal_cost_reserve"]
    assert r["terminal_futures_cash"] == 1500
    # Two 40-unit purchases: fee 8.004, slippage 4, terminal reserve 12.
    assert r["net"] == D("-24.004")
    for _ in range(1000):
        assert p.simulate([b], end_ms=p.H4, selected=selected) == result


def test_futures_unrealized_loss_reduces_entry_budget():
    a = account()
    a.futures_cash = D(200)
    short(a)
    a.day_start = D(8698)
    a.last_rates[1] = D(0)
    a.orders[1, "OPEN"] = p.Order("OPEN", D(102), D(0), 0, p.H4)
    m = minute(price="102")
    p._open(a, 1, m, 20 * p.DAY)
    new = a.shorts[1]
    # Existing short consumes 150 buffer and loses 2; available new cash is 48.
    assert new.qty * new.entry * D("1.5") + new.fees <= D(48)


def test_stop_cost_matches_hand_accounting_and_four_loss_classification():
    a = account()
    short(a)
    m = minute()
    m = replace(m, perp=(replace(m.perp[0], h=D(104)), m.perp[1]))
    p._minute(a, m, ((), ()), 20 * p.DAY)
    assert a.trades[0]["net"] == D("-3.303976175")
    assert a.streak == 1


@given(st.integers(min_value=50, max_value=1500))
def test_futures_cash_and_post_fee_margin_property(cash: int):
    a = account()
    a.futures_cash = D(cash)
    a.day_start = a.spot_cash + a.futures_cash
    a.last_rates[0] = D("-0.001")
    a.orders[0, "OPEN"] = p.Order("OPEN", D(100), D(0), 0, p.H4)
    tick(a, 0)
    assert a.shorts
    pos = a.shorts[0]
    eq = a.equity(minute().spot, minute().perp, "o")
    assert a.futures_cash >= D("1.5") * pos.qty * pos.entry
    assert pos.qty * pos.entry <= D("0.1") * eq


@pytest.mark.leakage
@pytest.mark.parametrize("field", ["target", "signal", "previous"])
def test_future_inputs_rejected(field: str):
    b = block(targets=(D("0.4"), D(0)))
    if field == "target":
        b = replace(b, target_as_of_ms=1)
    elif field == "signal":
        b = replace(b, signal_as_of_ms=1)
    else:
        b = replace(b, minutes=FixedMinutes(0, previous_offset_ms=p.MINUTE))
    with pytest.raises(CausalityError):
        p.simulate([b], end_ms=p.H4, selected=(p.cases()[0],))


@pytest.mark.leakage
def test_later_price_change_does_not_change_prefix_decisions():
    first = block(targets=(D("0.4"), D(0)))
    same = block(p.H4)
    changed = block(p.H4, price="101")
    case = (p.cases()[0],)
    a = p.simulate([first, same], end_ms=2 * p.H4, selected=case)[case[0].key]
    b = p.simulate([first, changed], end_ms=2 * p.H4, selected=case)[case[0].key]
    assert a["trace"][0] == b["trace"][0]
    assert [f for f in a["fills"] if f["at_ms"] < p.H4] == [
        f for f in b["fills"] if f["at_ms"] < p.H4
    ]


@pytest.mark.parametrize(
    "fault", ["duplicate_funding", "future_funding", "gap", "bad_bar", "empty", "case"]
)
def test_invalid_replay_inputs(fault: str):
    b = block()
    frames = [b]
    selected = (p.cases()[0],)
    if fault == "duplicate_funding":
        e = p.Funding(0, D(0), D(100), True)
        frames = [replace(b, funding=((e, e), ()))]
    elif fault == "future_funding":
        frames = [replace(b, funding=((p.Funding(p.H4, D(0), D(100), True),), ()))]
    elif fault == "gap":
        frames = [replace(b, at_ms=p.H4)]
    elif fault == "bad_bar":
        frames = [replace(b, spot=(bar("-1"), bar()))]
    elif fault == "empty":
        frames = []
    else:
        selected = (p.Case(D("0.1"), "BASE_WALLET", p.SCENARIOS[0]),)
    with pytest.raises(DataIntegrityError):
        p.simulate(frames, end_ms=p.H4, selected=selected)


def archive(
    path: Path, times: list[int], *, price: str = "100.00000000", volume: str = "1.00000000"
) -> None:
    rows = [
        f"{at},{price},{price},{price},{price},{volume},{at + 59999},100,1,0,0,0" for at in times
    ]
    with zipfile.ZipFile(path, "x") as target:
        target.writestr("source.csv", "\n".join(rows))


def test_source_inventory_reports_gaps_without_constructing_fills(tmp_path: Path):
    path = tmp_path / "BTCUSDT-1m-2020-06.zip"
    archive(path, [runner.START, runner.START + 2 * p.MINUTE])
    info = runner.inspect_source(path, runner.START, runner.START + 3 * p.MINUTE)
    assert info["rows"] == 2
    assert info["gaps"] == [
        {
            "start_ms": runner.START + p.MINUTE,
            "end_ms": runner.START + 2 * p.MINUTE,
            "missing_minutes": 1,
        }
    ]
    assert info["duplicate_or_out_of_order"] == []
    with pytest.raises(DataIntegrityError, match="gap/duplicate"):
        runner.read_source(path, runner.START, runner.START + 3 * p.MINUTE)


def test_source_duplicate_recorded_not_silently_deduplicated(tmp_path: Path):
    path = tmp_path / "BTCUSDT-1m-2020-06.zip"
    archive(path, [runner.START, runner.START])
    info = runner.inspect_source(path, runner.START, runner.START + p.MINUTE)
    assert info["rows"] == 2
    assert info["duplicate_or_out_of_order"] == [runner.START]


@pytest.mark.leakage
def test_source_preserved_period_rejected(tmp_path: Path):
    path = tmp_path / "BTCUSDT-1m-2026-09.zip"
    # Guard runs before touching the nonexistent forbidden archive.
    with pytest.raises(DataIntegrityError, match="preserved"):
        runner.inspect_source(path, runner.END, runner.END + p.MINUTE)


@pytest.mark.parametrize("text", ["NaN", "Infinity", "0.000000001", "1e30"])
def test_source_precision_rejects_nonexact_values(text: str):
    with pytest.raises(DataIntegrityError):
        runner.scaled(text)


def test_cache_exact_roundtrip_and_tamper_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "OUT", tmp_path)
    source = tmp_path / "BTCUSDT-1m-2020-06.zip"
    archive(source, [runner.START], price="123.12345678", volume="2.10000000")
    columns, info = runner.read_source(source, runner.START, runner.START + p.MINUTE)
    sha = runner.write_cache("test.bin", columns)
    metadata = {**info, "cache": "test.bin", "cache_sha256": sha}
    assert runner.cached(metadata) == columns
    assert columns[0][0] == 12312345678
    with pytest.raises(FileExistsError):
        runner.write_cache("test.bin", columns)
    with pytest.raises(DataIntegrityError, match="changed"):
        runner.cached({**metadata, "cache_sha256": "wrong"})


def test_inventory_guard_does_not_overwrite_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner, "OUT", tmp_path)
    runner.save("source_inventory.json", {"kept": True})
    with pytest.raises(DataIntegrityError, match="already"):
        runner.inventory()
    assert json.loads((tmp_path / "source_inventory.json").read_text()) == {"kept": True}
