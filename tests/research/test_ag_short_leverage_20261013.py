"""Synthetic accounting/causality only, never historical strategy performance."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.backtest.overlay import split_weights as legacy_split
from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from scripts import run_ag_short_leverage_20261013 as runner

D = Decimal


def bar(value: str = "100", volume: str = "100000") -> p.Bar:
    x = D(value)
    return p.Bar(x, x, x, x, D(volume))


def minute(at_ms: int = 0, value: str = "100") -> p.Minute:
    pair = (bar(value), bar(value))
    return p.Minute(at_ms, pair, pair, pair, pair, at_ms)


def account(exposure: int = 3, mode: p.Mode = "SHORT_MAX") -> p.Account:
    a = p.Account(p.Case(exposure, mode, p.SCENARIOS[0]))
    a.stop_refs = [D(80), D(80)]
    a.rates = [D(0), D(0)]
    a.targets_known = True
    return a


@dataclass
class Minutes:
    at_ms: int
    value: str = "100"

    def get(self, n: int) -> p.Minute:
        return minute(self.at_ms + n * p.MINUTE, self.value)


def block(
    at_ms: int = 0, targets: tuple[Decimal, Decimal] = (D("0.2"), D("0.2")), value: str = "100"
) -> p.Block:
    pair = (bar(value), bar(value))
    return p.Block(
        at_ms,
        pair,
        pair,
        pair,
        pair,
        targets,
        at_ms,
        (False, False),
        at_ms,
        ((), ()),
        Minutes(at_ms, value),
    )


def test_cases_are_fixed():
    assert len(p.cases()) == len({c.key for c in p.cases()}) == 24
    assert p.Case(3, "SHORT_MAX", p.SCENARIOS[0]).short_leverage == 3
    assert p.Case(4, "SHORT_1X", p.SCENARIOS[0]).short_leverage == 1


@pytest.mark.parametrize("exposure", [3, 4])
@pytest.mark.parametrize("weights", [("0", "0"), ("0.2", "0.2"), ("0.5", "0.5"), ("0.3", "0.4")])
def test_ag_allocation_matches_registered_equation(exposure, weights):
    inputs = (D(weights[0]), D(weights[1]))
    spot, future = p.split_weights(inputs, exposure)
    legacy_spot, legacy_future = legacy_split(
        {"A": float(inputs[0]), "B": float(inputs[1])}, exposure, exposure
    )
    for i, symbol in enumerate(("A", "B")):
        assert float(spot[i]) == pytest.approx(legacy_spot[symbol])
        assert float(future[i]) == pytest.approx(legacy_future[symbol])
        assert spot[i] + future[i] == inputs[i] * exposure


@pytest.mark.parametrize("exposure,mode", [(3, "SHORT_1X"), (3, "SHORT_MAX"), (4, "SHORT_MAX")])
def test_short_margin_and_risk_are_post_cost_bounded(exposure, mode):
    a = account(exposure, mode)
    m = minute()
    qty, info = p.short_size(a, 0, m, D("99.955"))
    assert qty > 0
    assert info["margin_total_after"] <= info["equity_after"] * D("0.10")
    assert info["risk"] <= info["equity_after"] * D("0.01")
    if mode == "SHORT_1X":
        assert qty * 100 < 1000
    else:
        assert 2500 < qty * 100 < 3000


def test_leverage_is_not_double_applied_to_pnl():
    values = []
    for exposure in (3, 4):
        a = account(exposure)
        p.future_fill(a, 0, D(-10), D(100), 0, "OPEN_SHORT")
        p.future_fill(a, 0, D(10), D(90), p.H4, "48H")
        values.append(a.trades[0]["net"])
        assert a.trades[0]["gross"] == 100
    assert values[0] == values[1]


def test_second_coin_cannot_reuse_short_margin():
    a = account(3)
    m = minute()
    qty, _ = p.short_size(a, 0, m, D("99.955"))
    p.future_fill(a, 0, -qty, D(100), 0, "OPEN_SHORT")
    second, info = p.short_size(a, 1, m, D("99.955"))
    assert second < qty
    assert info["margin_total_after"] <= info["equity_after"] * D("0.10")


def test_existing_long_consumes_collateral():
    a = account(3)
    before, _ = p.short_size(a, 0, minute(), D("99.955"))
    p.future_fill(a, 1, D(290), D(100), 0, "AG_REBALANCE")
    after, info = p.short_size(a, 0, minute(), D("99.955"))
    assert after < before
    assert info["binding"] in ("FREE_BUFFER", "TOTAL_FUTURES_CAP")


@pytest.mark.parametrize("side", [-1, 1])
def test_funding_sign_and_unknown_stop_credit(side):
    a = account()
    p.future_fill(a, 0, D(side * 10), D(100), 0, "OPEN")
    event = p.Funding(p.MINUTE, D("0.001"), D(100), True)
    p._fund(a, 0, event, uncertain_stop=True)
    assert a.funding[0] == (-1 if side > 0 else 0)


@pytest.mark.parametrize("side,price,expected", [(1, "70", "LONG_STOP"), (-1, "110", "SHORT_STOP")])
def test_gap_stop_is_adverse_and_closes_exactly(side, price, expected):
    a = account()
    p.future_fill(a, 0, D(side * 10), D(100), 0, "OPEN")
    p._minute(a, minute(p.MINUTE, price), ((), ()))
    assert 0 not in a.positions
    assert a.trades[0]["reason"] == expected
    assert a.trades[0]["net"] < 0


def test_no_direction_reversal_without_flat():
    a = account()
    p.future_fill(a, 0, D(-10), D(100), 0, "OPEN_SHORT")
    with pytest.raises(DataIntegrityError):
        p.future_fill(a, 0, D(11), D(100), p.MINUTE, "BAD_REVERSAL")


def test_long_priority_waits_for_exact_flat_and_60_minutes():
    a = account()
    p.future_fill(a, 0, D(-10), D(100), 0, "OPEN_SHORT")
    p.future_fill(a, 0, D(5), D(100), p.MINUTE, "LONG_PRIORITY")
    buy = p.Order("SPOT", D(1), D(100), p.DAY, "AG_REBALANCE")
    assert p._attempt(a, 0, buy, minute(2 * p.MINUTE))
    assert a.spot[0] == 0
    p.future_fill(a, 0, D(5), D(100), 3 * p.MINUTE, "LONG_PRIORITY")
    assert p._attempt(a, 0, buy, minute(4 * p.MINUTE))
    assert a.spot[0] == 0
    assert p._attempt(a, 0, buy, minute(64 * p.MINUTE))
    assert a.spot[0] == 1


@pytest.mark.parametrize("condition", ["LIMIT", "ZERO_VOLUME", "STALE", "HALT"])
def test_new_entry_denial(condition):
    a = account()
    m = minute()
    order = p.Order("SHORT", D(0), D(100), p.H4, "OPEN_SHORT")
    if condition == "LIMIT":
        m = minute(value="90")
    elif condition == "ZERO_VOLUME":
        m = replace(m, perp=(bar(volume="0"), bar()))
    elif condition == "STALE":
        m = replace(m, spot=(replace(bar(), observed=False), bar()))
    else:
        a.short_halted = True
    p._attempt(a, 0, order, m)
    assert not a.positions


def test_three_attempts_are_finite():
    a = account()
    order = p.Order("SHORT", D(0), D(100), p.H4, "OPEN_SHORT")
    for n in range(5):
        p._attempt(a, 0, order, minute(n * p.MINUTE, "90"))
    assert order.attempts == 3
    assert not a.positions


def test_loss_halt_does_not_block_reduction():
    a = account()
    p.future_fill(a, 0, D(-10), D(100), 0, "OPEN_SHORT")
    a.stopped = a.short_halted = True
    order = p.Order("FUTURE", D(0), D(100), p.H4, "ACCOUNT_STOP")
    assert p._attempt(a, 0, order, minute(2 * p.MINUTE))
    assert not a.positions


def test_short_halt_does_not_remove_existing_ag_long_policy():
    a = account()
    a.short_halted = True
    assert p._new_allowed(a, minute(), short=False)
    assert not p._new_allowed(a, minute(), short=True)


@pytest.mark.leakage
def test_future_signal_and_target_rejected():
    for b in (replace(block(), signal_as_of_ms=1), replace(block(), target_as_of_ms=1)):
        with pytest.raises(CausalityError):
            p.simulate([b], end_ms=p.H4)


@pytest.mark.leakage
def test_future_minute_timestamp_rejected():
    @dataclass
    class FutureMinutes:
        def get(self, n: int) -> p.Minute:
            return replace(minute(n * p.MINUTE), previous_as_of_ms=(n + 1) * p.MINUTE)

    with pytest.raises(CausalityError):
        p.simulate([replace(block(), minutes=FutureMinutes())], end_ms=p.H4)


@pytest.mark.leakage
def test_changing_future_prices_preserves_prefix():
    selected = (p.Case(3, "BASE", p.SCENARIOS[0]),)
    first = p.simulate([block(), block(p.H4)], end_ms=2 * p.H4, selected=selected)
    changed = p.simulate([block(), block(p.H4, value="120")], end_ms=2 * p.H4, selected=selected)
    key = selected[0].key
    assert first[key]["trace"][0] == changed[key]["trace"][0]
    assert [x for x in first[key]["fills"] if x["at_ms"] < p.H4] == [
        x for x in changed[key]["fills"] if x["at_ms"] < p.H4
    ]


def test_saved_ledger_independent_reconstruction():
    result = p.simulate(
        [block(), block(p.H4, value="110")],
        end_ms=2 * p.H4,
        selected=(p.Case(4, "BASE", p.SCENARIOS[0]),),
    )
    saved = json.loads(json.dumps(next(iter(result.values())), default=runner.serialize))
    assert runner.reconcile(saved)["saved_fill_reconciliation"] == "MATCH"
    saved["terminal_cash"] = str(D(saved["terminal_cash"]) + 1)
    with pytest.raises(DataIntegrityError):
        runner.reconcile(saved)


@given(st.integers(min_value=1, max_value=100), st.integers(min_value=60, max_value=140))
def test_signed_futures_cash_golden(quantity, exit_price):
    a = account()
    q = D(quantity)
    p.future_fill(a, 0, -q, D(100), 0, "OPEN_SHORT")
    p.future_fill(a, 0, q, D(exit_price), p.H4, "48H")
    assert abs(a.cash - p.INITIAL - a.trades[0]["net"]) < D("0.00000001")


def test_deterministic_synthetic_replay_1000_times():
    selected = (p.Case(3, "BASE", p.SCENARIOS[0]),)
    expected = p.simulate([block(targets=(D(0), D(0)))], end_ms=p.H4, selected=selected)
    for _ in range(1000):
        assert p.simulate([block(targets=(D(0), D(0)))], end_ms=p.H4, selected=selected) == expected


def test_block_cutoff_and_duplicate_funding_rejected():
    with pytest.raises(DataIntegrityError):
        p.simulate([block()], end_ms=p.H4 - 1)
    event = p.Funding(0, D("0.001"), D(100), True)
    with pytest.raises(DataIntegrityError):
        p.simulate([replace(block(), funding=((event, event), ()))], end_ms=p.H4)


def test_max3_and_max4_do_not_bypass_same_risk_budget():
    three, three_info = p.short_size(account(3), 0, minute(), D("99.955"))
    four, four_info = p.short_size(account(4), 0, minute(), D("99.955"))
    assert three == four
    assert three_info["binding"] == four_info["binding"] == "RISK1"
    assert four_info["margin"] < three_info["margin"]


@pytest.mark.parametrize("exposure", [3, 4])
def test_cost2_reduces_risk_limited_quantity(exposure):
    a = account(exposure)
    b = p.Account(p.Case(exposure, "SHORT_MAX", p.SCENARIOS[1]))
    b.rates = [D(0), D(0)]
    base, _ = p.short_size(a, 0, minute(), D("99.955"))
    doubled, info = p.short_size(b, 0, minute(), D("99.91"))
    assert doubled < base
    assert info["risk"] <= info["equity_after"] * D("0.01")


def test_adverse_funding_reduces_quantity_and_unknown_vetoes():
    a = account()
    base, _ = p.short_size(a, 0, minute(), D("99.955"))
    a.rates[0] = D("-0.005")
    stressed, _ = p.short_size(a, 0, minute(), D("99.955"))
    assert stressed < base
    a.rates[0] = None
    quantity, info = p.short_size(a, 0, minute(), D("99.955"))
    assert quantity == 0 and info["reason"] == "FUNDING_UNKNOWN"


def test_spot_buy_cannot_spend_long_margin():
    a = account()
    p.future_fill(a, 1, D(200), D(100), 0, "AG_REBALANCE")
    order = p.Order("SPOT", D(100), D(100), p.H4, "AG_REBALANCE")
    p._attempt(a, 0, order, minute(2 * p.MINUTE))
    assert 0 < a.spot[0] < 34
    long_margin, _ = a.margins(minute().perp)
    assert a.cash + min(D(0), a.unrealized(minute().perp)) >= long_margin


def test_partial_fill_is_not_full_target_or_flat():
    a = account()
    p.future_fill(a, 0, D(-10), D(100), 0, "OPEN_SHORT")
    order = p.Order("FUTURE", D(0), D(100), p.H4, "LONG_PRIORITY")
    m = minute(2 * p.MINUTE)
    m = replace(m, previous_perp=(bar(volume="1000"), bar()))
    assert not p._attempt(a, 0, order, m)
    assert a.positions[0].qty == 9
    assert a.flat_ack[0] < 0


def test_deleverage_is_requested_without_new_exposure():
    a = account()
    p.future_fill(a, 0, D(300), D(100), 0, "AG_REBALANCE")
    p._begin(a, block(p.H4), 20 * p.DAY)
    assert a.orders[0, "FUTURE"].reason == "DELEVERAGE"
    assert abs(a.orders[0, "FUTURE"].wanted) < 300
    assert all(o.kind != "SHORT" for o in a.orders.values())


def test_initial_capital_stop_is_not_peak_drawdown_stop():
    a = account()
    a.peak = D(30000)
    p._begin(a, block(), 20 * p.DAY)
    assert a.short_halted and not a.stopped
    a.cash = D(2999)
    p._begin(a, block(), 20 * p.DAY)
    assert a.stopped


def test_missing_targets_fail_closed():
    a = account()
    p._begin(a, replace(block(), targets=None), 20 * p.DAY)
    assert not p._new_allowed(a, minute(), short=False)
    assert not p._new_allowed(a, minute(), short=True)


@pytest.mark.parametrize("offset", [1, 2, 3, 4, 5])
def test_no_update_preserves_confirmed_daily_target(offset):
    a = account()
    p._begin(a, block(), 20 * p.DAY)
    prior = a.goals
    at_ms = offset * p.H4
    p._begin(a, replace(block(at_ms), targets=None), 20 * p.DAY)
    assert a.targets_known and a.goals == prior
    assert a.targets_updated_ms == 0
    assert p._new_allowed(a, minute(at_ms), short=False)


def test_zero_daily_goal_carry_allows_registered_short():
    a = account()
    p._begin(a, block(targets=(D(0), D(0))), 20 * p.DAY)
    p._begin(a, replace(block(p.H4), targets=None, signals=(True, False)), 20 * p.DAY)
    p._minute(a, minute(p.H4), ((), ()))
    assert a.targets_known and a.positions[0].side == -1
    assert a.counts["short_opened"] == 1


def test_missing_next_daily_update_expires_without_rewriting_goal():
    a = account()
    p._begin(a, block(), 20 * p.DAY)
    p._begin(a, replace(block(p.DAY), targets=None), 20 * p.DAY)
    assert not a.targets_known and a.goals == (D("0.2"), D("0.2"))
    assert not p._new_allowed(a, minute(p.DAY), short=False)
    assert not p._new_allowed(a, minute(p.DAY), short=True)


def test_next_day_refresh_replaces_previous_goal():
    a = account()
    p._begin(a, block(), 20 * p.DAY)
    p._begin(a, block(p.DAY, targets=(D(0), D("0.1"))), 20 * p.DAY)
    p._begin(a, replace(block(p.DAY + p.H4), targets=None), 20 * p.DAY)
    assert a.targets_known and a.goals == (D(0), D("0.1"))
    assert a.targets_updated_ms == p.DAY


@pytest.mark.leakage
def test_future_target_refresh_does_not_change_carried_prefix():
    selected = (p.Case(3, "BASE", p.SCENARIOS[0]),)
    prefix = [block(), replace(block(p.H4), targets=None)]
    first = p.simulate([*prefix, block(2 * p.H4)], end_ms=3 * p.H4, selected=selected)
    changed = p.simulate(
        [*prefix, block(2 * p.H4, targets=(D(0), D(0)))], end_ms=3 * p.H4, selected=selected
    )
    key = selected[0].key
    assert first[key]["trace"][:2] == changed[key]["trace"][:2]
    assert [f for f in first[key]["fills"] if f["at_ms"] < 2 * p.H4] == [
        f for f in changed[key]["fills"] if f["at_ms"] < 2 * p.H4
    ]


@pytest.mark.parametrize(
    "exposure,mode", [(3, "SHORT_1X"), (3, "SHORT_MAX"), (4, "SHORT_1X"), (4, "SHORT_MAX")]
)
def test_total_futures_cap_includes_entry_cost(exposure, mode):
    a = account(exposure, mode)
    m = minute()
    quantity, _ = p.short_size(a, 0, m, D("99.955"))
    p.future_fill(a, 0, -quantity, D(100), 0, "OPEN_SHORT")
    collateral = a.cash + min(D(0), a.unrealized(m.perp))
    assert quantity * 100 <= exposure * collateral


def test_ack5_keeps_orders_serialized():
    a = p.Account(p.Case(3, "BASE", p.SCENARIOS[3]))
    a.targets_known = True
    a.stop_refs = [D(80), D(80)]
    p.spot_fill(a, 0, D(1), D(100), 0, "AG_REBALANCE")
    order = p.Order("FUTURE", D(1), D(100), p.H4, "AG_REBALANCE")
    assert not p._attempt(a, 1, order, minute(4 * p.MINUTE))
    assert not a.positions
    assert p._attempt(a, 1, order, minute(5 * p.MINUTE))
    assert a.positions[1].qty == 1


def test_entry_minute_stop_is_not_skipped():
    a = account()
    a.orders[0, "SHORT"] = p.Order("SHORT", D(0), D(100), p.H4, "OPEN_SHORT")
    m = minute()
    m = replace(m, perp=(replace(bar(), h=D(110)), bar()))
    p._minute(a, m, ((), ()))
    assert a.counts["short_opened"] == a.counts["short_closed"] == 1
    assert not a.positions and a.trades[0]["reason"] == "SHORT_STOP"


@pytest.mark.leakage
def test_later_intraminute_funding_cannot_change_entry_size():
    a, b = account(), account()
    for item in (a, b):
        item.orders[0, "SHORT"] = p.Order("SHORT", D(0), D(100), p.H4, "OPEN_SHORT")
    event = p.Funding(p.MINUTE // 2, D("-0.01"), D(100), True)
    p._minute(a, minute(), ((), ()))
    p._minute(b, minute(), ((event,), ()))
    assert a.positions[0].opened_qty == b.positions[0].opened_qty
    assert b.funding[0] < 0 and b.rates[0] == event.rate


def test_funding_after_normal_close_not_charged_to_closed_position():
    a = account()
    p.future_fill(a, 0, D(-10), D(100), 0, "OPEN_SHORT")
    a.orders[0, "FUTURE"] = p.Order("FUTURE", D(0), D(100), p.H4, "48H")
    event = p.Funding(2 * p.MINUTE + p.MINUTE // 2, D("-0.01"), D(100), True)
    p._minute(a, minute(2 * p.MINUTE), ((event,), ()))
    assert not a.positions and a.funding[0] == 0
