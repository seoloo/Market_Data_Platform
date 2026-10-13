"""Synthetic Phase 0 model, not remote execution / durability / strategy PnL."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TypedDict, Unpack

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import ConfigError, DataIntegrityError
from auto_trading.research.long_return_contract_20261013 import (
    Evidence,
    FlatAnchor,
    Gates,
    Journal,
    LimitIntent,
    OrderStatus,
    Policy,
    Reservation,
    Scope,
    Target,
    Wallets,
    claim_limit_intent,
    claim_return,
    episode_id,
    evaluate_return,
    funding_cash,
    observe_order,
    short_quantity,
    wallet_equity,
)

D = Decimal
NOW_MS = 1_000_000
COOLDOWN_MS = 3_600_000
SCOPE = Scope("synthetic", "offline", "fixed-contract-v1", "BTCUSDT")
ALLOWED = Gates(True, True, True, True, True, True, True)
DEFAULT_QUANTITY = D(10)


class EvidenceChanges(TypedDict, total=False):
    remote_quantity: Decimal
    local_quantity: Decimal
    orders_settled: bool
    claims_settled: bool
    reconciled: bool
    last_short_fill_ms: int | None
    persisted: bool
    owned_stops_remaining: int


def evidence(**changes: Unpack[EvidenceChanges]) -> Evidence:
    initial = Evidence(
        scope=SCOPE,
        as_of_ms=NOW_MS,
        remote_scope=SCOPE,
        remote_quantity=D(0),
        local_quantity=D(0),
        remote_observed_ms=NOW_MS,
        last_short_fill_ms=NOW_MS - 1000,
        orders_settled=True,
        claims_settled=True,
        reconciled=True,
        ownership_known=True,
        owned_stops_remaining=0,
        persisted=True,
        clock_ok=True,
        target=Target(D(10), NOW_MS - 60_000, NOW_MS, NOW_MS + 86_400_000, True),
        held_spot_quantity=D(0),
        quote_price=D(100),
        quote_observed_ms=NOW_MS,
        episode_reference_price=D(100),
        step=D("0.001"),
        min_quantity=D("0.001"),
        min_notional=D(5),
        current_volume=D(100),
        volume_observed_ms=NOW_MS,
        wallets=Wallets(D(9000), D(1000)),
        gates=ALLOWED,
        snapshot_digest="synthetic-snapshot",
        code_version="frozen-contract",
        config_version="synthetic-only",
    )
    return replace(initial, **changes)


def tick(e: Evidence, delta_ms: int) -> Evidence:
    at_ms = e.as_of_ms + delta_ms
    return replace(
        e,
        as_of_ms=at_ms,
        remote_observed_ms=at_ms,
        quote_observed_ms=at_ms,
        volume_observed_ms=at_ms,
    )


def ready() -> tuple[Evidence, FlatAnchor]:
    e = evidence()
    anchor = evaluate_return(e).anchor
    assert anchor is not None
    return tick(e, COOLDOWN_MS), anchor


def intent(symbol: str = "BTCUSDT", quantity: Decimal = DEFAULT_QUANTITY) -> LimitIntent:
    return LimitIntent(
        replace(SCOPE, symbol=symbol), NOW_MS, "LONG_BUY", 0, quantity, D(100), D(100)
    )


def claimed() -> Journal:
    result = claim_limit_intent(
        Journal(Wallets(D(9000), D(1000))),
        intent(),
        expected_generation=0,
        commit_confirmed=True,
    )
    assert result.dispatch_once
    return result.journal


def test_l01_done_below_step_is_still_short_residual() -> None:
    out = evaluate_return(evidence(remote_quantity=D("-0.00001"), local_quantity=D("-0.00001")))
    assert out.state == "SHORT_RESIDUAL"
    assert out.buy_quantity == 0 and out.reduce_short and not out.cleanup_owned_protection


def test_l02_local_filled_is_not_remote_flat() -> None:
    out = evaluate_return(evidence(remote_quantity=D("-0.001")))
    assert out.state == "RECONCILE" and out.reduce_short and out.buy_quantity == 0


@pytest.mark.parametrize("field", ["orders_settled", "claims_settled", "reconciled"])
def test_l03_open_inflight_or_unreconciled_zero_blocks_long(field: str) -> None:
    out = evaluate_return(
        evidence(
            orders_settled=field != "orders_settled",
            claims_settled=field != "claims_settled",
            reconciled=field != "reconciled",
        )
    )
    assert out.state == "FLAT_UNSETTLED" and out.anchor is None and out.buy_quantity == 0


def test_l04_cancel_receipt_does_not_settle_late_open() -> None:
    pending = evaluate_return(evidence(orders_settled=False))
    late = evaluate_return(evidence(remote_quantity=D("-0.2"), local_quantity=D("-0.2")))
    assert pending.buy_quantity == late.buy_quantity == 0
    assert late.reduce_short and not late.cleanup_owned_protection


def test_l05_60minute_boundary_is_eligibility_not_fill() -> None:
    e = evidence()
    first = evaluate_return(e)
    before = evaluate_return(tick(e, COOLDOWN_MS - 1), first.anchor)
    after = evaluate_return(tick(e, COOLDOWN_MS), first.anchor)
    assert before.state == "COOLDOWN" and before.buy_quantity == 0
    assert after.state == "LONG_READY" and after.buy_quantity == 10


def test_l06_partial_close_does_not_start_clock() -> None:
    for qty in (D("-0.3"), D("-0.2"), D("-0.01")):
        assert evaluate_return(evidence(remote_quantity=qty, local_quantity=qty)).anchor is None
    e = evidence(last_short_fill_ms=NOW_MS)
    anchor = evaluate_return(e).anchor
    assert anchor is not None and anchor.started_ms == NOW_MS
    assert evaluate_return(tick(e, COOLDOWN_MS - 1), anchor).state == "COOLDOWN"


def test_repeated_flat_checks_do_not_restart_cooldown() -> None:
    e = evidence()
    anchor = evaluate_return(e).anchor
    for minute in range(1, 61):
        out = evaluate_return(tick(e, minute * 60_000), anchor)
        assert out.anchor == anchor
    assert out.state == "LONG_READY"


def test_l07_same_intent_100_times_and_ten_serial_restarts_dispatch_once() -> None:
    e, anchor = ready()
    journal = Journal(e.wallets)
    dispatches = 0
    for _ in range(100):
        decision, result = claim_return(
            e,
            anchor,
            journal,
            expected_generation=journal.generation,
            commit_confirmed=True,
        )
        dispatches += int(result.dispatch_once)
        journal = result.journal
        assert decision.buy_quantity <= 10
    for _ in range(10):
        journal = replace(journal, wallets=replace(journal.wallets))
        _, result = claim_return(
            e,
            anchor,
            journal,
            expected_generation=journal.generation,
            commit_confirmed=True,
        )
        dispatches += int(result.dispatch_once)
        journal = result.journal
    assert dispatches == len(journal.records) == len(journal.wallets.reservations) == 1
    assert journal.wallets.available("spot") == D(7999)


def test_l08_unknown_and_absent_lookup_do_not_release_or_retry() -> None:
    journal = claimed()
    unknown = observe_order(
        journal,
        intent().client_id,
        expected_generation=1,
        status="UNKNOWN",
    )
    absent = observe_order(
        unknown,
        intent().client_id,
        expected_generation=2,
        status=None,
    )
    assert absent == unknown and absent.unresolved
    retry = claim_limit_intent(
        absent,
        replace(intent(), attempt=1),
        expected_generation=2,
        commit_confirmed=True,
    )
    assert not retry.dispatch_once and retry.journal.wallets == journal.wallets


def test_l09_duplicate_fill_fee_once_reverse_cumulative_quarantined() -> None:
    journal = claimed()
    partial = observe_order(
        journal,
        intent().client_id,
        expected_generation=1,
        status="PARTIAL",
        cumulative_quantity=D(4),
        cumulative_quote=D(400),
        cumulative_fee=D("0.4"),
        fee_asset="USDT",
    )
    duplicate = observe_order(
        partial,
        intent().client_id,
        expected_generation=2,
        status="PARTIAL",
        cumulative_quantity=D(4),
        cumulative_quote=D(400),
        cumulative_fee=D("0.4"),
        fee_asset="USDT",
    )
    assert duplicate.wallets == partial.wallets
    assert duplicate.wallets.spot_cash == D("8599.6")
    reverse = observe_order(
        duplicate,
        intent().client_id,
        expected_generation=3,
        status="PARTIAL",
        cumulative_quantity=D(3),
        cumulative_quote=D(300),
        cumulative_fee=D("0.3"),
        fee_asset="USDT",
    )
    assert (
        reverse.halted
        and reverse.records == duplicate.records
        and reverse.wallets == duplicate.wallets
    )


def test_l10_serial_generation_conflict_and_late_worker_fail_closed() -> None:
    journal = claimed()
    losing = claim_limit_intent(
        journal, intent("ETHUSDT"), expected_generation=0, commit_confirmed=True
    )
    assert not losing.dispatch_once and losing.journal == journal
    late = observe_order(
        journal,
        intent().client_id,
        expected_generation=0,
        status="FILLED",
        cumulative_quantity=D(10),
        cumulative_quote=D(1000),
        cumulative_fee=D(1),
        fee_asset="USDT",
    )
    assert late.halted and late.records == journal.records and late.wallets == journal.wallets


def test_l11_commit_failure_has_no_claim_or_cash_change() -> None:
    original = Journal(Wallets(D(9000), D(1000)))
    result = claim_limit_intent(original, intent(), expected_generation=0, commit_confirmed=False)
    assert not result.dispatch_once and result.journal == original
    assert evaluate_return(evidence(persisted=False)).buy_quantity == 0


def test_l12_protection_cleanup_only_after_confirmed_flat() -> None:
    residual = evaluate_return(
        evidence(
            remote_quantity=D("-0.1"),
            local_quantity=D("-0.1"),
            owned_stops_remaining=1,
        )
    )
    flat = evaluate_return(evidence(owned_stops_remaining=1))
    assert residual.reduce_short and not residual.cleanup_owned_protection
    assert flat.cleanup_owned_protection and flat.anchor is None and flat.buy_quantity == 0
    assert replace(intent(), purpose="SHORT_CLOSE").reduce_only


def test_l13_unknown_scope_ownership_and_other_symbol_claim_block() -> None:
    e, anchor = ready()
    assert evaluate_return(replace(e, ownership_known=False), anchor).buy_quantity == 0
    assert (
        evaluate_return(
            replace(e, remote_scope=replace(SCOPE, symbol="ETHUSDT")), anchor
        ).buy_quantity
        == 0
    )
    other = claim_limit_intent(
        Journal(e.wallets),
        intent("ETHUSDT"),
        expected_generation=0,
        commit_confirmed=True,
    ).journal
    _, claim = claim_return(e, anchor, other, expected_generation=1, commit_confirmed=True)
    assert not claim.dispatch_once and len(claim.journal.records) == 1


@pytest.mark.parametrize("change", ["zero", "expired", "unconfirmed", "future"])
def test_l14_invalid_target_cancels_waiting_long_not_risk_reduction(change: str) -> None:
    e, anchor = ready()
    target = {
        "zero": replace(e.target, quantity=D(0)),
        "expired": replace(e.target, expires_ms=e.as_of_ms),
        "unconfirmed": replace(e.target, confirmed=False),
        "future": replace(e.target, observed_ms=e.as_of_ms + 1),
    }[change]
    out = evaluate_return(replace(e, target=target), anchor)
    assert out.cancel_pending_long and out.buy_quantity == 0
    reducing = evaluate_return(
        replace(e, target=target, remote_quantity=D(-1), local_quantity=D(-1))
    )
    assert reducing.reduce_short and reducing.cancel_pending_long


def test_l15_partial_fill_then_canceled_remaining_reserve_and_latest_goal() -> None:
    journal = observe_order(
        claimed(),
        intent().client_id,
        expected_generation=1,
        status="CANCELED",
        cumulative_quantity=D(4),
        cumulative_quote=D(400),
        cumulative_fee=D("0.4"),
        fee_asset="USDT",
    )
    assert journal.wallets.spot_cash == D("8599.6") and not journal.wallets.reservations
    e, anchor = ready()
    reduced = evaluate_return(
        replace(e, held_spot_quantity=D(4), target=replace(e.target, quantity=D(6))), anchor
    )
    assert reduced.buy_quantity == 2
    zero = evaluate_return(
        replace(e, held_spot_quantity=D(4), target=replace(e.target, quantity=D(0))), anchor
    )
    assert zero.cancel_pending_long and zero.buy_quantity == 0


def test_l16_two_symbols_cannot_double_spend_same_spot_cash() -> None:
    first = claim_limit_intent(
        Journal(Wallets(D(1500), D(1000))),
        intent(),
        expected_generation=0,
        commit_confirmed=True,
    )
    second = claim_limit_intent(
        first.journal,
        intent("ETHUSDT"),
        expected_generation=1,
        commit_confirmed=True,
    )
    assert first.dispatch_once and not second.dispatch_once
    assert first.journal.wallets.available("spot") == D(499)


def test_l17_futures_cash_is_not_spot_buy_cash() -> None:
    e, anchor = ready()
    out = evaluate_return(replace(e, wallets=Wallets(D(0), D(10000))), anchor)
    assert out.buy_quantity == 0 and "WALLET_RESERVATION" in out.reasons


def test_l18_reserved_cash_cannot_be_reused_or_unconfirmed_transfer_counted() -> None:
    e, anchor = ready()
    wallets = Wallets(D(1000), D(9000), (Reservation("other", "spot", D(999)),))
    assert evaluate_return(replace(e, wallets=wallets), anchor).buy_quantity == 0
    assert wallets.spot_cash == 1000 and wallets.futures_cash == 9000
    assert wallet_equity(wallets, D(0), D(0)) == 10000


@pytest.mark.parametrize("cash,expected", [(D(10000), D(10)), (D(900), D(6))])
def test_l19_margin_ten_percent_and_buffer_limit_one_x(cash: Decimal, expected: Decimal) -> None:
    quantity = short_quantity(
        equity=D(10000),
        available_futures=cash,
        remaining_gross=D(10000),
        price=D(100),
        step=D("0.001"),
        stop_distance_pct=D("0.03"),
        roundtrip_cost_pct=D("0.002"),
        filter_notional_cap=D(10000),
    )
    assert quantity == expected and quantity * D(100) <= D(1000)


@pytest.mark.parametrize(
    "gates", [replace(ALLOWED, account=False), Gates(), replace(ALLOWED, cost=None)]
)
def test_l20_risk_failure_or_missing_gates_block_entry_not_close(gates: Gates) -> None:
    e, anchor = ready()
    out = evaluate_return(replace(e, gates=gates), anchor)
    assert out.buy_quantity == 0 and out.reasons
    residual = evaluate_return(replace(e, gates=gates, remote_quantity=D(-1), local_quantity=D(-1)))
    assert residual.reduce_short
    longer = evaluate_return(replace(e, account_cooldown_until_ms=e.as_of_ms + 1), anchor)
    assert longer.state == "COOLDOWN"


@pytest.mark.parametrize("kind", ["stale", "cap", "zero_volume", "volume_stale"])
def test_l21_stale_quote_cap_or_zero_volume_no_entry(kind: str) -> None:
    e, anchor = ready()
    bad = {
        "stale": replace(e, quote_observed_ms=e.as_of_ms - 5001),
        "cap": replace(e, quote_price=D("100.301")),
        "zero_volume": replace(e, current_volume=D(0)),
        "volume_stale": replace(e, volume_observed_ms=e.as_of_ms - 5001),
    }[kind]
    assert evaluate_return(bad, anchor).buy_quantity == 0


def test_l22_funding_signed_algebra_no_spot_funding_assumed() -> None:
    assert funding_cash(D(-2), D(100), D("0.001")) == D("0.2")
    assert funding_cash(D(-2), D(100), D("-0.001")) == D("-0.2")
    assert funding_cash(D(0), D(100), D("0.001")) == 0


def test_l23_hand_accounting_reservation_not_equity_expense() -> None:
    e, anchor = ready()
    decision, result = claim_return(
        e, anchor, Journal(e.wallets), expected_generation=0, commit_confirmed=True
    )
    journal = result.journal
    assert decision.state == "LONG_READY" and result.dispatch_once
    assert wallet_equity(journal.wallets, D(0), D(0)) == 10000
    filled = observe_order(
        journal,
        intent().client_id,
        expected_generation=1,
        status="FILLED",
        cumulative_quantity=D(10),
        cumulative_quote=D(1000),
        cumulative_fee=D(1),
        fee_asset="USDT",
    )
    assert filled.wallets.spot_cash == 7999 and filled.wallets.futures_cash == 1000
    assert wallet_equity(filled.wallets, D(1000), D(0)) == 9999
    assert not filled.wallets.reservations and not filled.unresolved
    duplicate = observe_order(
        filled,
        intent().client_id,
        expected_generation=2,
        status="FILLED",
        cumulative_quantity=D(10),
        cumulative_quote=D(1000),
        cumulative_fee=D(1),
        fee_asset="USDT",
    )
    assert duplicate.wallets == filled.wallets


@pytest.mark.leakage
@pytest.mark.parametrize("kind", ["future_remote", "clock", "future_quote", "future_volume"])
def test_l24_future_evidence_or_clock_failure_fail_closed(kind: str) -> None:
    e, anchor = ready()
    bad = {
        "future_remote": replace(e, remote_observed_ms=e.as_of_ms + 1),
        "clock": replace(e, clock_ok=False),
        "future_quote": replace(e, quote_observed_ms=e.as_of_ms + 1),
        "future_volume": replace(e, volume_observed_ms=e.as_of_ms + 1),
    }[kind]
    assert evaluate_return(bad, anchor).buy_quantity == 0


@pytest.mark.leakage
def test_future_updates_cannot_mutate_previous_decision_or_cash() -> None:
    e, anchor = ready()
    old = evaluate_return(e, anchor)
    changed_future = replace(tick(e, 60_000), quote_price=D(120))
    assert evaluate_return(changed_future, anchor).buy_quantity == 0
    assert evaluate_return(e, anchor) == old and e.wallets.spot_cash == 9000


def test_own_long_pending_keeps_flat_anchor_but_never_dispatches_twice() -> None:
    e, anchor = ready()
    decision, result = claim_return(
        e,
        anchor,
        Journal(e.wallets),
        expected_generation=0,
        commit_confirmed=True,
    )
    assert result.dispatch_once
    pending, duplicate = claim_return(
        tick(e, 60_000),
        decision.anchor,
        result.journal,
        expected_generation=1,
        commit_confirmed=True,
    )
    assert pending.anchor == anchor and pending.state == "LONG_ORDER_PENDING"
    assert not duplicate.dispatch_once and duplicate.journal == result.journal


@pytest.mark.parametrize("fee_asset,fee", [("BNB", D(1)), (None, None)])
def test_unknown_or_unconverted_fee_quarantines_without_releasing(
    fee_asset: str | None, fee: Decimal | None
) -> None:
    journal = claimed()
    out = observe_order(
        journal,
        intent().client_id,
        expected_generation=1,
        status="FILLED",
        cumulative_quantity=D(10),
        cumulative_quote=D(1000),
        cumulative_fee=fee,
        fee_asset=fee_asset,
    )
    assert out.halted and out.wallets == journal.wallets and out.records == journal.records


def test_different_parameters_same_id_are_quarantined() -> None:
    journal = claimed()
    result = claim_limit_intent(
        journal, replace(intent(), quantity=D(9)), expected_generation=1, commit_confirmed=True
    )
    assert (
        not result.dispatch_once
        and result.journal.halted
        and result.journal.wallets == journal.wallets
    )


def test_next_attempt_cannot_reset_episode_price_reference() -> None:
    canceled = observe_order(
        claimed(),
        intent().client_id,
        expected_generation=1,
        status="CANCELED",
        cumulative_fee=D(0),
        fee_asset="USDT",
    )
    result = claim_limit_intent(
        canceled,
        replace(intent(), attempt=1, reference_price=D(200), limit_price=D(200)),
        expected_generation=2,
        commit_confirmed=True,
    )
    assert not result.dispatch_once and result.reason == "EPISODE_REFERENCE_CHANGED"
    assert result.journal.halted and result.journal.wallets == canceled.wallets


def test_cheap_early_fill_cannot_hide_later_fill_over_limit() -> None:
    partial = observe_order(
        claimed(),
        intent().client_id,
        expected_generation=1,
        status="PARTIAL",
        cumulative_quantity=D(5),
        cumulative_quote=D(490),
        cumulative_fee=D("0.49"),
        fee_asset="USDT",
    )
    # Aggregate average 100 is within cap; the new piece 510/5 = 102 is not.
    bad_piece = observe_order(
        partial,
        intent().client_id,
        expected_generation=2,
        status="FILLED",
        cumulative_quantity=D(10),
        cumulative_quote=D(1000),
        cumulative_fee=D(1),
        fee_asset="USDT",
    )
    assert (
        bad_piece.halted
        and bad_piece.records == partial.records
        and bad_piece.wallets == partial.wallets
    )


def test_new_short_fill_invalidates_previous_flat_anchor() -> None:
    e, anchor = ready()
    out = evaluate_return(replace(e, last_short_fill_ms=e.as_of_ms - 1), anchor)
    assert out.state == "COOLDOWN" and out.anchor is not None
    assert out.anchor.started_ms == e.as_of_ms


@pytest.mark.parametrize("policy", ["cooldown", "margin", "risk", "buffer", "cap"])
def test_safety_constraints_cannot_be_relaxed(policy: str) -> None:
    with pytest.raises(ConfigError):
        if policy == "cooldown":
            Policy(cooldown_ms=0)
        elif policy == "margin":
            Policy(margin_pct=D("0.25"))
        elif policy == "risk":
            Policy(risk_pct=D("0.02"))
        elif policy == "buffer":
            Policy(margin_buffer=D(1))
        else:
            Policy(cap_pct=D("0.01"))


def test_nonfinite_decimal_and_negative_effective_time_rejected() -> None:
    with pytest.raises(DataIntegrityError):
        funding_cash(D("NaN"), D(100), D("0.001"))
    with pytest.raises(DataIntegrityError):
        episode_id(SCOPE, -1)


@given(st.lists(st.integers(min_value=0, max_value=10), min_size=1, max_size=30))
def test_property_monotone_fills_never_duplicate_spending(values: list[int]) -> None:
    journal = claimed()
    maximum = 0
    for value in values:
        maximum = max(maximum, value)
        quantity = D(maximum)
        quote = quantity * 100
        status: OrderStatus = "FILLED" if maximum == 10 else "PARTIAL"
        journal = observe_order(
            journal,
            intent().client_id,
            expected_generation=journal.generation,
            status=status,
            cumulative_quantity=quantity,
            cumulative_quote=quote,
            cumulative_fee=quote * D("0.001"),
            fee_asset="USDT",
        )
        assert not journal.halted
        assert journal.wallets.spot_cash == D(9000) - quote * D("1.001")
        assert journal.wallets.available("spot") == D(7999)


@given(st.integers(min_value=1, max_value=1000), st.integers(min_value=1, max_value=1000))
def test_property_distinct_scope_ids_are_stable(a: int, b: int) -> None:
    left = replace(SCOPE, symbol=f"symbol-{a}")
    right = replace(SCOPE, symbol=f"symbol-{b}")
    assert episode_id(left, NOW_MS) == episode_id(left, NOW_MS)
    assert (episode_id(left, NOW_MS) == episode_id(right, NOW_MS)) == (a == b)
