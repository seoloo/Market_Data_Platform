"""Pure direction-switch contract; no broker, historical PnL or operating integration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from auto_trading.core.errors import CausalityError, DataIntegrityError


class Direction(StrEnum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


class Action(StrEnum):
    WAIT = "WAIT"
    CLOSE = "CLOSE"
    OPEN = "OPEN"


@dataclass(frozen=True)
class Signal:
    direction: Direction
    signal_id: str
    confirmed_at_ms: int


@dataclass(frozen=True)
class Evidence:
    as_of_ms: int
    asset: str
    position_epoch: str
    held: Direction
    remaining_qty: Decimal
    unresolved_order: bool = False
    flat_confirmed_at_ms: int | None = None
    balance_fresh: bool = False
    signal_fresh: bool = False
    trading_halted: bool = False


@dataclass(frozen=True)
class Policy:
    long_budget_pct: Decimal
    short_budget_pct: Decimal
    reentry_wait_min: int = 60


@dataclass(frozen=True)
class State:
    wanted: Direction = Direction.FLAT
    signal_id: str = ""
    confirmed_at_ms: int = -1
    conflict: bool = False


@dataclass(frozen=True)
class Plan:
    action: Action
    direction: Direction
    budget_pct: Decimal
    intent_id: str
    reason: str
    state: State


def _identity(parts: tuple[str, ...]) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def decide_switch(state: State, evidence: Evidence, policy: Policy, signal: Signal | None) -> Plan:
    """Close the old direction before opening the latest confirmed opposite direction.

    Ratios are directional cash ceilings, not simultaneous holdings or overrides of risk sizing.
    Remote flat/order evidence is explicit input; this model does not manufacture that evidence.
    """
    if (
        not evidence.remaining_qty.is_finite()
        or evidence.remaining_qty < 0
        or (evidence.held == Direction.FLAT) != (evidence.remaining_qty == 0)
        or not evidence.asset
        or not evidence.position_epoch
        or evidence.as_of_ms < 0
    ):
        raise DataIntegrityError("invalid switching position evidence")
    if (
        not all(
            v.is_finite() and 0 < v <= 1 for v in (policy.long_budget_pct, policy.short_budget_pct)
        )
        or policy.long_budget_pct + policy.short_budget_pct != 1
        or policy.reentry_wait_min < 60
    ):
        raise DataIntegrityError("invalid switching budget/cooldown")
    flat_ms = evidence.flat_confirmed_at_ms
    if flat_ms is not None and (flat_ms < 0 or flat_ms > evidence.as_of_ms):
        raise CausalityError("future/invalid flat evidence")
    next_state = state
    if signal is not None:
        if signal.confirmed_at_ms > evidence.as_of_ms or signal.confirmed_at_ms < 0:
            raise CausalityError("future/invalid switching signal")
        if not signal.signal_id:
            raise DataIntegrityError("signal identity required")
        if signal.signal_id == state.signal_id and (
            signal.confirmed_at_ms != state.confirmed_at_ms
            or (not state.conflict and signal.direction != state.wanted)
        ):
            raise DataIntegrityError("conflicting signal identity")
        if signal.confirmed_at_ms > state.confirmed_at_ms:
            next_state = State(signal.direction, signal.signal_id, signal.confirmed_at_ms)
        elif signal.confirmed_at_ms == state.confirmed_at_ms and signal.direction != state.wanted:
            next_state = State(Direction.FLAT, state.signal_id, state.confirmed_at_ms, True)

    def waiting(reason: str) -> Plan:
        return Plan(Action.WAIT, Direction.FLAT, Decimal(0), "", reason, next_state)

    # UNKNOWN/in-flight requests cannot be bypassed by a later signal or a new order ID.
    if evidence.unresolved_order:
        return waiting("UNRESOLVED_ORDER")
    if evidence.held != Direction.FLAT and (
        evidence.held != next_state.wanted or evidence.trading_halted or next_state.conflict
    ):
        return Plan(
            Action.CLOSE,
            evidence.held,
            Decimal(0),
            _identity((evidence.asset, evidence.position_epoch, "CLOSE")),
            "CLOSE_OLD_DIRECTION",
            next_state,
        )
    if next_state.conflict:
        return waiting("SIMULTANEOUS_CONFLICT")
    if next_state.wanted == Direction.FLAT or evidence.held == next_state.wanted:
        return waiting("NO_DIRECTION_CHANGE")
    if evidence.trading_halted or not evidence.balance_fresh or not evidence.signal_fresh:
        return waiting("NEW_RISK_BLOCKED")
    if flat_ms is None:
        return waiting("FLAT_NOT_CONFIRMED")
    if evidence.as_of_ms < flat_ms + policy.reentry_wait_min * 60_000:
        return waiting("REENTRY_WAIT")
    budget = (
        policy.long_budget_pct if next_state.wanted == Direction.LONG else policy.short_budget_pct
    )
    return Plan(
        Action.OPEN,
        next_state.wanted,
        budget,
        _identity((evidence.asset, evidence.position_epoch, next_state.signal_id, "OPEN")),
        "OPEN_LATEST_DIRECTION",
        next_state,
    )
