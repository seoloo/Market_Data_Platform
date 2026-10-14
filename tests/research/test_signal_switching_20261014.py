from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research.signal_switching_20261014 import (
    Action,
    Direction,
    Evidence,
    Policy,
    Signal,
    State,
    decide_switch,
)

WAIT_MS = 60 * 60_000
POLICY = Policy(Decimal("0.7"), Decimal("0.3"))
HELD = Evidence(
    100,
    "BTCUSDT",
    "position-1",
    Direction.LONG,
    Decimal("0.1"),
    balance_fresh=True,
    signal_fresh=True,
)


@pytest.mark.parametrize(
    "old,new", [(Direction.LONG, Direction.SHORT), (Direction.SHORT, Direction.LONG)]
)
def test_both_switch_directions_close_before_open(old: Direction, new: Direction) -> None:
    evidence = replace(HELD, held=old)
    signal = Signal(new, "signal-1", 100)
    closing = decide_switch(State(), evidence, POLICY, signal)
    assert closing.action == Action.CLOSE
    flat = replace(
        evidence, held=Direction.FLAT, remaining_qty=Decimal(0), flat_confirmed_at_ms=100
    )
    assert decide_switch(closing.state, flat, POLICY, None).reason == "REENTRY_WAIT"
    opening = decide_switch(closing.state, replace(flat, as_of_ms=100 + WAIT_MS), POLICY, None)
    assert opening.action == Action.OPEN and opening.direction == new
    assert opening.budget_pct == (Decimal("0.7") if new == Direction.LONG else Decimal("0.3"))


@given(st.decimals(min_value="0.00000001", max_value="100", allow_nan=False, allow_infinity=False))
def test_any_nonzero_residue_requires_close(qty: Decimal) -> None:
    plan = decide_switch(
        State(), replace(HELD, remaining_qty=qty), POLICY, Signal(Direction.SHORT, "s", 100)
    )
    assert plan.action == Action.CLOSE


def test_unknown_cannot_be_bypassed_by_new_signal() -> None:
    plan = decide_switch(
        State(), replace(HELD, unresolved_order=True), POLICY, Signal(Direction.SHORT, "s", 100)
    )
    assert plan.action == Action.WAIT and plan.reason == "UNRESOLVED_ORDER"


def test_close_id_stable_across_changed_pending_direction() -> None:
    first = decide_switch(State(), HELD, POLICY, Signal(Direction.SHORT, "s", 100))
    second = decide_switch(
        first.state, replace(HELD, as_of_ms=101), POLICY, Signal(Direction.FLAT, "f", 101)
    )
    assert first.intent_id == second.intent_id


def test_new_signal_can_replace_pending_direction_after_flat() -> None:
    closing = decide_switch(State(), HELD, POLICY, Signal(Direction.SHORT, "s", 100))
    flat = replace(
        HELD,
        held=Direction.FLAT,
        remaining_qty=Decimal(0),
        flat_confirmed_at_ms=100,
        as_of_ms=100 + WAIT_MS,
    )
    plan = decide_switch(closing.state, flat, POLICY, Signal(Direction.LONG, "l", flat.as_of_ms))
    assert plan.action == Action.OPEN and plan.direction == Direction.LONG


def test_halt_and_stale_block_open_but_allow_close() -> None:
    signal = Signal(Direction.SHORT, "s", 100)
    assert (
        decide_switch(
            State(), replace(HELD, trading_halted=True, balance_fresh=False), POLICY, signal
        ).action
        == Action.CLOSE
    )
    flat = replace(
        HELD,
        held=Direction.FLAT,
        remaining_qty=Decimal(0),
        flat_confirmed_at_ms=0,
        as_of_ms=WAIT_MS,
    )
    assert (
        decide_switch(State(), replace(flat, trading_halted=True), POLICY, signal).action
        == Action.WAIT
    )
    assert (
        decide_switch(State(), replace(flat, balance_fresh=False), POLICY, signal).action
        == Action.WAIT
    )


def test_same_timestamp_conflict_fails_closed() -> None:
    state = State(Direction.LONG, "l", 100)
    plan = decide_switch(state, HELD, POLICY, Signal(Direction.SHORT, "s", 100))
    assert plan.action == Action.CLOSE and plan.state.conflict


def test_open_identity_reproducible_1000_times() -> None:
    flat = replace(
        HELD,
        held=Direction.FLAT,
        remaining_qty=Decimal(0),
        flat_confirmed_at_ms=100,
        as_of_ms=100 + WAIT_MS,
    )
    plans = [
        decide_switch(State(), flat, POLICY, Signal(Direction.SHORT, "s", 100)) for _ in range(1000)
    ]
    assert all(p.action == Action.OPEN for p in plans)
    assert len({p.intent_id for p in plans}) == 1


@pytest.mark.leakage
def test_future_signal_and_future_flat_rejected() -> None:
    with pytest.raises(CausalityError):
        decide_switch(State(), HELD, POLICY, Signal(Direction.SHORT, "s", 101))
    with pytest.raises(CausalityError):
        decide_switch(State(), replace(HELD, flat_confirmed_at_ms=101), POLICY, None)


def test_bad_evidence_and_policy_rejected() -> None:
    with pytest.raises(DataIntegrityError):
        decide_switch(State(), replace(HELD, held=Direction.FLAT), POLICY, None)
    with pytest.raises(DataIntegrityError):
        decide_switch(State(), HELD, Policy(Decimal("0.7"), Decimal("0.4")), None)
    with pytest.raises(DataIntegrityError):
        decide_switch(State(), HELD, replace(POLICY, reentry_wait_min=0), None)


def test_old_signal_does_not_undo_new_direction() -> None:
    state = State(Direction.SHORT, "s", 100)
    plan = decide_switch(state, HELD, POLICY, Signal(Direction.LONG, "old", 99))
    assert plan.state == state and plan.action == Action.CLOSE


def test_missing_freshness_proof_never_opens() -> None:
    flat = Evidence(WAIT_MS, "QQQ", "p", Direction.FLAT, Decimal(0), flat_confirmed_at_ms=0)
    plan = decide_switch(State(), flat, POLICY, Signal(Direction.LONG, "l", 0))
    assert plan.action == Action.WAIT and plan.reason == "NEW_RISK_BLOCKED"


def test_conflict_repeats_stay_closed_until_newer_signal() -> None:
    state = State(Direction.LONG, "l", 100)
    conflict = decide_switch(state, HELD, POLICY, Signal(Direction.SHORT, "s", 100)).state
    repeated = decide_switch(conflict, HELD, POLICY, Signal(Direction.LONG, "l", 100))
    assert repeated.state.conflict and repeated.action == Action.CLOSE
    newer = decide_switch(
        conflict, replace(HELD, as_of_ms=101), POLICY, Signal(Direction.LONG, "next", 101)
    )
    assert not newer.state.conflict
