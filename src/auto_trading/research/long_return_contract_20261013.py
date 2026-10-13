"""Pure synthetic contract model. No broker, DB, scheduler, or market replay.

The immutable journal models a serialized authoritative store, not a durable
database or a concurrent production CAS. Explicit synthetic evidence is required.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from auto_trading.core.errors import ConfigError, DataIntegrityError, InvariantViolationError
from auto_trading.core.money import floor_to_step

ZERO = Decimal(0)
ONE = Decimal(1)
WalletName = Literal["spot", "futures"]
Purpose = Literal["LONG_BUY", "SHORT_CLOSE"]
OrderStatus = Literal["SUBMITTING", "UNKNOWN", "NEW", "PARTIAL", "FILLED", "CANCELED", "REJECTED"]
TERMINAL = frozenset({"FILLED", "CANCELED", "REJECTED"})


def _number(value: Decimal, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DataIntegrityError("finite Decimal required")
    if value < ZERO or (positive and value == ZERO):
        raise DataIntegrityError("invalid nonnegative/positive amount")


@dataclass(frozen=True)
class Policy:
    cooldown_ms: int = 3_600_000
    freshness_ms: int = 5_000
    cap_pct: Decimal = Decimal("0.003")
    spot_fee_pct: Decimal = Decimal("0.001")
    margin_pct: Decimal = Decimal("0.10")
    risk_pct: Decimal = Decimal("0.01")
    margin_buffer: Decimal = Decimal("1.5")

    def __post_init__(self) -> None:
        if self.cooldown_ms < 3_600_000 or self.freshness_ms <= 0:
            raise ConfigError("cooldown cannot be relaxed; freshness must be positive")
        for value in (self.cap_pct, self.spot_fee_pct, self.margin_pct, self.risk_pct):
            _number(value)
        _number(self.margin_buffer, positive=True)
        if (
            self.cap_pct > Decimal("0.003")
            or self.margin_pct > Decimal("0.10")
            or self.risk_pct > Decimal("0.01")
            or self.margin_buffer < Decimal("1.5")
        ):
            raise ConfigError("synthetic safety ceilings cannot be relaxed")


DEFAULT_POLICY = Policy()


@dataclass(frozen=True)
class Scope:
    account_alias: str
    environment: str
    strategy_version: str
    symbol: str

    def __post_init__(self) -> None:
        if not all((self.account_alias, self.environment, self.strategy_version, self.symbol)):
            raise DataIntegrityError("complete nonsecret scope required")


def episode_id(scope: Scope, effective_ms: int) -> str:
    if effective_ms < 0:
        raise DataIntegrityError("negative effective time")
    payload = json.dumps(
        [
            scope.account_alias,
            scope.environment,
            scope.strategy_version,
            scope.symbol,
            effective_ms,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Reservation:
    intent_id: str
    wallet: WalletName
    amount: Decimal


@dataclass(frozen=True)
class Wallets:
    spot_cash: Decimal
    futures_cash: Decimal
    reservations: tuple[Reservation, ...] = ()

    def available(self, wallet: WalletName) -> Decimal:
        _number(self.spot_cash)
        _number(self.futures_cash)
        keys: set[str] = set()
        for reservation in self.reservations:
            _number(reservation.amount)
            if reservation.intent_id in keys:
                raise InvariantViolationError("duplicate reservation")
            keys.add(reservation.intent_id)
        balance = self.spot_cash if wallet == "spot" else self.futures_cash
        reserved = sum((r.amount for r in self.reservations if r.wallet == wallet), ZERO)
        return balance - reserved


@dataclass(frozen=True)
class Gates:
    system: bool | None = None
    data: bool | None = None
    account: bool | None = None
    exposure: bool | None = None
    market: bool | None = None
    schedule: bool | None = None
    cost: bool | None = None

    def vetoes(self) -> tuple[str, ...]:
        values = (
            self.system,
            self.data,
            self.account,
            self.exposure,
            self.market,
            self.schedule,
            self.cost,
        )
        return tuple(f"G{i}" for i, value in enumerate(values, 1) if value is not True)


@dataclass(frozen=True)
class Target:
    quantity: Decimal
    observed_ms: int
    effective_ms: int
    expires_ms: int
    confirmed: bool


@dataclass(frozen=True)
class Evidence:
    scope: Scope
    as_of_ms: int
    remote_scope: Scope
    remote_quantity: Decimal
    local_quantity: Decimal
    remote_observed_ms: int
    last_short_fill_ms: int | None
    orders_settled: bool
    claims_settled: bool
    reconciled: bool
    ownership_known: bool
    owned_stops_remaining: int
    persisted: bool
    clock_ok: bool
    target: Target
    held_spot_quantity: Decimal
    quote_price: Decimal
    quote_observed_ms: int
    episode_reference_price: Decimal
    step: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    current_volume: Decimal
    volume_observed_ms: int
    wallets: Wallets
    gates: Gates
    account_cooldown_until_ms: int = 0
    snapshot_digest: str = ""
    code_version: str = ""
    config_version: str = ""


@dataclass(frozen=True)
class FlatAnchor:
    scope: Scope
    started_ms: int
    last_short_fill_ms: int


@dataclass(frozen=True)
class ReturnDecision:
    state: str
    reasons: tuple[str, ...]
    anchor: FlatAnchor | None
    buy_quantity: Decimal = ZERO
    reduce_short: bool = False
    cleanup_owned_protection: bool = False
    cancel_pending_long: bool = False


def evaluate_return(
    evidence: Evidence, anchor: FlatAnchor | None = None, policy: Policy = DEFAULT_POLICY
) -> ReturnDecision:
    """Evaluate already-observed evidence. No fills, timestamps, or gates are invented."""
    e = evidence
    for value in (e.remote_quantity, e.local_quantity):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise DataIntegrityError("invalid signed quantity")
    target = e.target
    _number(target.quantity)
    target_valid = (
        target.confirmed
        and 0 <= target.observed_ms <= e.as_of_ms
        and 0 <= target.effective_ms <= e.as_of_ms < target.expires_ms
        and target.quantity > ZERO
    )
    cancel_long = not target_valid
    fresh = (
        e.clock_ok
        and e.remote_scope == e.scope
        and 0 <= e.remote_observed_ms <= e.as_of_ms
        and e.as_of_ms - e.remote_observed_ms <= policy.freshness_ms
    )
    if not fresh:
        return ReturnDecision(
            "RECONCILE", ("REMOTE_SCOPE_TIME",), None, cancel_pending_long=cancel_long
        )
    if e.remote_quantity != e.local_quantity or e.remote_quantity > ZERO:
        return ReturnDecision(
            "RECONCILE",
            ("POSITION_MISMATCH",),
            None,
            reduce_short=e.remote_quantity < ZERO,
            cancel_pending_long=cancel_long,
        )
    if e.remote_quantity < ZERO:
        return ReturnDecision(
            "SHORT_RESIDUAL",
            ("EXACT_ZERO_REQUIRED",),
            None,
            reduce_short=True,
            cancel_pending_long=cancel_long,
        )
    reasons: list[str] = []
    for name, satisfied in (
        ("ORDERS_UNSETTLED", e.orders_settled),
        ("CLAIMS_UNSETTLED", e.claims_settled),
        ("RECONCILIATION", e.reconciled),
        ("OWNERSHIP", e.ownership_known),
        ("PERSISTENCE", e.persisted),
    ):
        if not satisfied:
            reasons.append(name)
    if e.last_short_fill_ms is None or not 0 <= e.last_short_fill_ms <= e.remote_observed_ms:
        reasons.append("FILL_TIME_UNKNOWN")
    if e.owned_stops_remaining != 0:
        reasons.append("PROTECTION_UNSETTLED")
    if not all((e.snapshot_digest, e.code_version, e.config_version)):
        reasons.append("AUDIT_ID_MISSING")
    if reasons:
        return ReturnDecision(
            "FLAT_UNSETTLED",
            tuple(reasons),
            None,
            cleanup_owned_protection=(
                e.owned_stops_remaining > 0
                and e.orders_settled
                and e.claims_settled
                and e.ownership_known
                and e.reconciled
            ),
            cancel_pending_long=cancel_long,
        )
    assert e.last_short_fill_ms is not None
    if (
        anchor is None
        or anchor.scope != e.scope
        or anchor.last_short_fill_ms != e.last_short_fill_ms
        or anchor.started_ms > e.as_of_ms
    ):
        anchor = FlatAnchor(e.scope, max(e.last_short_fill_ms, e.as_of_ms), e.last_short_fill_ms)
    if not target_valid:
        return ReturnDecision("LONG_BLOCKED", ("TARGET_INVALID",), anchor, cancel_pending_long=True)
    if e.as_of_ms < max(anchor.started_ms + policy.cooldown_ms, e.account_cooldown_until_ms):
        return ReturnDecision("COOLDOWN", ("COOLDOWN",), anchor)
    reasons.extend(e.gates.vetoes())
    for value in (e.held_spot_quantity, e.min_quantity, e.min_notional, e.current_volume):
        _number(value)
    for value in (e.quote_price, e.episode_reference_price, e.step):
        _number(value, positive=True)
    if not 0 <= e.quote_observed_ms <= e.as_of_ms:
        reasons.append("QUOTE_TIME")
    elif e.as_of_ms - e.quote_observed_ms > policy.freshness_ms:
        reasons.append("QUOTE_STALE")
    if e.quote_price > e.episode_reference_price * (ONE + policy.cap_pct):
        reasons.append("EPISODE_PRICE_CAP")
    if e.current_volume == ZERO:
        reasons.append("NO_TRADING")
    if not 0 <= e.volume_observed_ms <= e.as_of_ms:
        reasons.append("VOLUME_TIME")
    elif e.as_of_ms - e.volume_observed_ms > policy.freshness_ms:
        reasons.append("VOLUME_STALE")
    available = e.wallets.available("spot")
    if available <= ZERO or e.wallets.available("futures") < ZERO:
        reasons.append("WALLET_RESERVATION")
    remaining = max(ZERO, target.quantity - e.held_spot_quantity)
    if remaining == ZERO:
        return ReturnDecision("LONG_TARGET_REACHED", tuple(reasons), anchor)
    quantity = floor_to_step(
        min(remaining, max(ZERO, available) / (e.quote_price * (ONE + policy.spot_fee_pct))), e.step
    )
    if quantity == ZERO or quantity < e.min_quantity or quantity * e.quote_price < e.min_notional:
        reasons.append("BELOW_MIN")
    if reasons:
        return ReturnDecision("LONG_BLOCKED", tuple(reasons), anchor)
    return ReturnDecision("LONG_READY", (), anchor, buy_quantity=quantity)


def short_quantity(
    *,
    equity: Decimal,
    available_futures: Decimal,
    remaining_gross: Decimal,
    price: Decimal,
    step: Decimal,
    stop_distance_pct: Decimal,
    roundtrip_cost_pct: Decimal,
    filter_notional_cap: Decimal,
    policy: Policy = DEFAULT_POLICY,
) -> Decimal:
    """Fixed 1x synthetic min-sizing, not a full operating risk implementation."""
    for value in (
        equity,
        available_futures,
        remaining_gross,
        roundtrip_cost_pct,
        filter_notional_cap,
    ):
        _number(value)
    for value in (price, step, stop_distance_pct):
        _number(value, positive=True)
    notional = min(
        equity * Decimal("0.25"),
        equity * policy.margin_pct,
        equity * policy.risk_pct / (stop_distance_pct + roundtrip_cost_pct),
        available_futures / policy.margin_buffer,
        remaining_gross,
        filter_notional_cap,
    )
    return floor_to_step(notional / price, step)


def funding_cash(signed_quantity: Decimal, mark: Decimal, rate: Decimal) -> Decimal:
    """Futures-only funding algebra. Caller supplies settlement-held quantity."""
    _number(mark, positive=True)
    if not all(isinstance(v, Decimal) and v.is_finite() for v in (signed_quantity, rate)):
        raise DataIntegrityError("finite funding inputs required")
    return -signed_quantity * mark * rate


def wallet_equity(wallets: Wallets, spot_value: Decimal, futures_unrealized: Decimal) -> Decimal:
    _number(spot_value)
    if not isinstance(futures_unrealized, Decimal) or not futures_unrealized.is_finite():
        raise DataIntegrityError("finite unrealized required")
    wallets.available("spot")
    wallets.available("futures")
    return wallets.spot_cash + wallets.futures_cash + spot_value + futures_unrealized


@dataclass(frozen=True)
class LimitIntent:
    scope: Scope
    effective_ms: int
    purpose: Purpose
    attempt: int
    quantity: Decimal
    limit_price: Decimal
    reference_price: Decimal

    def __post_init__(self) -> None:
        if self.purpose not in ("LONG_BUY", "SHORT_CLOSE") or self.attempt < 0:
            raise DataIntegrityError("only explicit capped-limit purposes supported")
        for value in (self.quantity, self.limit_price, self.reference_price):
            _number(value, positive=True)

    @property
    def reduce_only(self) -> bool:
        return self.purpose == "SHORT_CLOSE"

    @property
    def client_id(self) -> str:
        return f"{episode_id(self.scope, self.effective_ms)}-{self.purpose}-{self.attempt}"


@dataclass(frozen=True)
class OrderRecord:
    intent: LimitIntent
    status: OrderStatus = "SUBMITTING"
    cumulative_quantity: Decimal = ZERO
    cumulative_quote: Decimal = ZERO
    cumulative_fee: Decimal = ZERO


@dataclass(frozen=True)
class Journal:
    wallets: Wallets
    generation: int = 0
    records: tuple[OrderRecord, ...] = ()
    halted: bool = False

    @property
    def unresolved(self) -> bool:
        return any(record.status not in TERMINAL for record in self.records)


@dataclass(frozen=True)
class ClaimResult:
    journal: Journal
    dispatch_once: bool
    reason: str


def claim_limit_intent(
    journal: Journal,
    intent: LimitIntent,
    *,
    expected_generation: int,
    commit_confirmed: bool,
    policy: Policy = DEFAULT_POLICY,
) -> ClaimResult:
    """A pure, serialized CAS specification. Does not commit or dispatch anything."""
    for record in journal.records:
        if record.intent.client_id == intent.client_id:
            if record.intent != intent:
                return ClaimResult(replace(journal, halted=True), False, "ID_PARAMETER_CONFLICT")
            return ClaimResult(journal, False, "ALREADY_CLAIMED")
    if not commit_confirmed or journal.generation != expected_generation:
        return ClaimResult(journal, False, "COMMIT_OR_GENERATION")
    if journal.halted or journal.unresolved:
        return ClaimResult(journal, False, "UNSETTLED_JOURNAL")
    for record in journal.records:
        previous = record.intent
        same_episode = (
            previous.scope == intent.scope
            and previous.effective_ms == intent.effective_ms
            and previous.purpose == intent.purpose
        )
        if same_episode and previous.reference_price != intent.reference_price:
            return ClaimResult(replace(journal, halted=True), False, "EPISODE_REFERENCE_CHANGED")
    for value in (intent.quantity, intent.limit_price, intent.reference_price):
        _number(value, positive=True)
    if intent.attempt < 0 or intent.limit_price > intent.reference_price * (ONE + policy.cap_pct):
        return ClaimResult(journal, False, "INTENT_OR_PRICE_CAP")
    wallets = journal.wallets
    if intent.purpose == "LONG_BUY":
        reserve = intent.quantity * intent.limit_price * (ONE + policy.spot_fee_pct)
        if reserve > wallets.available("spot"):
            return ClaimResult(journal, False, "SPOT_CASH")
        wallets = replace(
            wallets,
            reservations=(*wallets.reservations, Reservation(intent.client_id, "spot", reserve)),
        )
    return ClaimResult(
        replace(
            journal,
            wallets=wallets,
            generation=journal.generation + 1,
            records=(*journal.records, OrderRecord(intent)),
        ),
        True,
        "SYNTHETIC_CLAIM_ONLY",
    )


def observe_order(
    journal: Journal,
    client_id: str,
    *,
    expected_generation: int,
    status: OrderStatus | None,
    cumulative_quantity: Decimal = ZERO,
    cumulative_quote: Decimal = ZERO,
    cumulative_fee: Decimal | None = None,
    fee_asset: str | None = None,
    commit_confirmed: bool = True,
    policy: Policy = DEFAULT_POLICY,
) -> Journal:
    """Apply cumulative synthetic spot accounting once. Unknown evidence keeps reservations."""
    if not commit_confirmed:
        return replace(journal, halted=True)
    if expected_generation != journal.generation:
        return replace(journal, halted=True)
    matches = [r for r in journal.records if r.intent.client_id == client_id]
    if len(matches) != 1:
        return replace(journal, halted=True)
    record = matches[0]
    if status is None:
        return journal  # One absent lookup is not proof of non-placement.
    if status == "UNKNOWN":
        if record.status in TERMINAL:
            return replace(journal, halted=True)
        return replace(
            journal,
            generation=journal.generation + 1,
            records=tuple(
                replace(r, status="UNKNOWN") if r == record else r for r in journal.records
            ),
        )
    if cumulative_fee is None or fee_asset != "USDT":
        return replace(journal, halted=True)
    for value in (cumulative_quantity, cumulative_quote, cumulative_fee):
        _number(value)
    inconsistent = (
        cumulative_quantity < record.cumulative_quantity
        or cumulative_quote < record.cumulative_quote
        or cumulative_fee < record.cumulative_fee
        or cumulative_quantity > record.intent.quantity
        or (status == "FILLED" and cumulative_quantity != record.intent.quantity)
        or (
            record.status in TERMINAL
            and (
                status != record.status
                or cumulative_quantity != record.cumulative_quantity
                or cumulative_quote != record.cumulative_quote
                or cumulative_fee != record.cumulative_fee
            )
        )
        or (
            cumulative_quantity == record.cumulative_quantity
            and (
                cumulative_quote != record.cumulative_quote
                or cumulative_fee != record.cumulative_fee
            )
        )
        or cumulative_quote > cumulative_quantity * record.intent.limit_price
        or cumulative_quote - record.cumulative_quote
        > (cumulative_quantity - record.cumulative_quantity) * record.intent.limit_price
        or (cumulative_quantity > ZERO and cumulative_quote == ZERO)
        or cumulative_fee > cumulative_quote * policy.spot_fee_pct
    )
    if inconsistent:
        return replace(journal, halted=True)
    wallets = journal.wallets
    if record.intent.purpose == "LONG_BUY":
        spent = cumulative_quote - record.cumulative_quote + cumulative_fee - record.cumulative_fee
        cash = wallets.spot_cash - spent
        if cash < ZERO:
            return replace(journal, halted=True)
        reservations = tuple(r for r in wallets.reservations if r.intent_id != client_id)
        if status not in TERMINAL:
            remainder = (record.intent.quantity - cumulative_quantity) * record.intent.limit_price
            reservations = (
                *reservations,
                Reservation(client_id, "spot", remainder * (ONE + policy.spot_fee_pct)),
            )
        wallets = replace(wallets, spot_cash=cash, reservations=reservations)
    updated = replace(
        record,
        status=status,
        cumulative_quantity=cumulative_quantity,
        cumulative_quote=cumulative_quote,
        cumulative_fee=cumulative_fee,
    )
    return replace(
        journal,
        wallets=wallets,
        generation=journal.generation + 1,
        records=tuple(updated if r == record else r for r in journal.records),
    )


def claim_return(
    evidence: Evidence,
    anchor: FlatAnchor | None,
    journal: Journal,
    *,
    expected_generation: int,
    commit_confirmed: bool,
    policy: Policy = DEFAULT_POLICY,
) -> tuple[ReturnDecision, ClaimResult]:
    """Compose evidence and journal gates. Still no actual order or fill is produced."""
    futures_unsettled = any(
        r.intent.purpose == "SHORT_CLOSE" and r.status not in TERMINAL for r in journal.records
    )
    e = replace(
        evidence,
        wallets=journal.wallets,
        claims_settled=(evidence.claims_settled and not futures_unsettled and not journal.halted),
    )
    decision = evaluate_return(e, anchor, policy)
    if journal.unresolved:
        # Own spot BUY does not undo a proven futures close or restart its timer.
        # It still blocks another dispatch, including a different symbol.
        if decision.state == "LONG_READY":
            decision = replace(
                decision,
                state="LONG_ORDER_PENDING",
                buy_quantity=ZERO,
                reasons=(*decision.reasons, "UNSETTLED_LONG_ORDER"),
            )
        return decision, ClaimResult(journal, False, "UNSETTLED_JOURNAL")
    if decision.state != "LONG_READY":
        return decision, ClaimResult(journal, False, "RETURN_NOT_READY")
    intent = LimitIntent(
        e.scope,
        e.target.effective_ms,
        "LONG_BUY",
        0,
        decision.buy_quantity,
        e.quote_price,
        e.episode_reference_price,
    )
    return decision, claim_limit_intent(
        journal,
        intent,
        expected_generation=expected_generation,
        commit_confirmed=commit_confirmed,
        policy=policy,
    )
