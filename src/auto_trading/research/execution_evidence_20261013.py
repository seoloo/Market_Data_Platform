"""Pure evidence audit, not an order router, risk engine, or profitability test."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from auto_trading.core.errors import CausalityError, DataIntegrityError, StaleDataError
from auto_trading.core.money import ceil_to_step, floor_to_step

Side = Literal["BUY", "SELL"]
ZERO = Decimal(0)
BPS = Decimal(10000)


def positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= ZERO:
        raise DataIntegrityError(f"invalid positive {name}")


@dataclass(frozen=True)
class Scope:
    venue: str
    strategy: str
    symbol: str


@dataclass(frozen=True)
class Rules:
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass(frozen=True)
class QuotePolicy:
    max_age_ms: int = 1000
    max_clock_uncertainty_ms: int = 250
    max_mark_skew_ms: int = 1000
    max_spread_bps: Decimal = Decimal(10)
    cap_bps: Decimal = Decimal(30)


DEFAULT_QUOTE_POLICY = QuotePolicy()


@dataclass(frozen=True)
class Depth:
    symbol: str
    exchange_ms: int
    received_ms: int
    clock_offset_ms: int | None
    clock_uncertainty_ms: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]


@dataclass(frozen=True)
class Mark:
    symbol: str
    price: Decimal
    received_ms: int
    exchange_ms: int | None = None


@dataclass(frozen=True)
class DepthEstimate:
    status: str
    requested_qty: Decimal
    indicative_qty: Decimal
    remaining_qty: Decimal
    indicative_vwap: Decimal | None
    limit_price: Decimal
    spread_bps: Decimal
    visible_usage: Decimal


def capped_limit(mark: Decimal, side: Side, tick: Decimal, cap_bps: Decimal) -> Decimal:
    positive(mark, "mark")
    positive(tick, "tick")
    if side not in ("BUY", "SELL") or not cap_bps.is_finite() or not ZERO < cap_bps < BPS:
        raise DataIntegrityError("invalid side/cap")
    if side == "BUY":
        return floor_to_step(mark * (1 + cap_bps / BPS), tick)
    return ceil_to_step(mark * (1 - cap_bps / BPS), tick)


def estimate_depth(
    depth: Depth,
    mark: Mark,
    rules: Rules,
    side: Side,
    requested_qty: Decimal,
    *,
    as_of_ms: int,
    visible_usage: Decimal,
    policy: QuotePolicy = DEFAULT_QUOTE_POLICY,
) -> DepthEstimate:
    """Visible-book assumption only: even 25% is not a guaranteed fill lower bound."""
    if depth.symbol not in ("BTCUSDT", "ETHUSDT") or mark.symbol != depth.symbol:
        raise DataIntegrityError("symbol scope")
    if min(depth.received_ms, mark.received_ms, depth.exchange_ms) < 0:
        raise DataIntegrityError("negative timestamp")
    if max(depth.received_ms, mark.received_ms) > as_of_ms:
        raise CausalityError("quote received after decision")
    if min(policy.max_age_ms, policy.max_clock_uncertainty_ms, policy.max_mark_skew_ms) < 0:
        raise DataIntegrityError("invalid time policy")
    if (
        depth.clock_offset_ms is None
        or not 0 <= depth.clock_uncertainty_ms <= policy.max_clock_uncertainty_ms
    ):
        raise DataIntegrityError("clock unknown/uncertain")
    event_local_ms = depth.exchange_ms - depth.clock_offset_ms
    event_age_ms = depth.received_ms - event_local_ms
    if mark.exchange_ms is None or mark.exchange_ms < 0:
        raise DataIntegrityError("mark exchange time unknown")
    mark_age_ms = mark.received_ms - (mark.exchange_ms - depth.clock_offset_ms)
    if event_age_ms < -depth.clock_uncertainty_ms:
        raise CausalityError("exchange clock ahead beyond uncertainty")
    if mark_age_ms < -depth.clock_uncertainty_ms:
        raise CausalityError("mark clock ahead beyond uncertainty")
    if (
        as_of_ms - min(depth.received_ms, mark.received_ms) > policy.max_age_ms
        or event_age_ms + depth.clock_uncertainty_ms > policy.max_age_ms
        or mark_age_ms + depth.clock_uncertainty_ms > policy.max_age_ms
        or abs(depth.received_ms - mark.received_ms) > policy.max_mark_skew_ms
    ):
        raise StaleDataError("quote stale/skewed")
    for value in (rules.tick, rules.step, rules.min_qty, rules.min_notional, requested_qty):
        positive(value, "quantity/filter")
    if visible_usage not in (Decimal(1), Decimal("0.25")):
        raise DataIntegrityError("unregistered visible depth usage")
    for levels, descending in ((depth.bids, True), (depth.asks, False)):
        if not levels:
            raise DataIntegrityError("empty side")
        prior: Decimal | None = None
        for price, qty in levels:
            positive(price, "level price")
            positive(qty, "level quantity")
            if price != floor_to_step(price, rules.tick) or qty != floor_to_step(qty, rules.step):
                raise DataIntegrityError("level precision")
            if prior is not None and (price >= prior if descending else price <= prior):
                raise DataIntegrityError("level ordering/duplicate")
            prior = price
    bid, ask = depth.bids[0][0], depth.asks[0][0]
    if bid >= ask:
        raise DataIntegrityError("crossed book")
    positive(policy.max_spread_bps, "max spread")
    spread_bps = (ask - bid) / ((ask + bid) / 2) * BPS
    if spread_bps > policy.max_spread_bps:
        raise DataIntegrityError("spread limit")
    limit = capped_limit(mark.price, side, rules.tick, policy.cap_bps)
    requested = floor_to_step(requested_qty, rules.step)
    if requested < rules.min_qty or requested * limit < rules.min_notional:
        return DepthEstimate(
            "BELOW_MIN", requested, ZERO, requested, None, limit, spread_bps, visible_usage
        )
    remaining, quote = requested, ZERO
    for price, qty in depth.asks if side == "BUY" else depth.bids:
        if (side == "BUY" and price > limit) or (side == "SELL" and price < limit):
            break
        take = min(remaining, floor_to_step(qty * visible_usage, rules.step))
        remaining -= take
        quote += take * price
        if remaining == ZERO:
            break
    indicative = requested - remaining
    return DepthEstimate(
        "INDICATIVE_ONLY",
        requested,
        indicative,
        remaining,
        quote / indicative if indicative else None,
        limit,
        spread_bps,
        visible_usage,
    )


@dataclass(frozen=True)
class OrderEvidence:
    scope: Scope
    intent_id: str
    order_id: str
    side: Side
    requested_qty: Decimal
    intent_qty: Decimal
    cumulative_qty: Decimal
    limit_price: Decimal
    decision_ms: int
    observed_ms: int
    state: str


@dataclass(frozen=True)
class FillEvidence:
    scope: Scope
    order_id: str
    trade_id: str
    side: Side
    qty: Decimal
    price: Decimal
    fee: Decimal | None
    fee_asset: str | None
    exchange_ms: int
    received_ms: int


@dataclass(frozen=True)
class EvidenceAudit:
    as_of_ms: int
    complete: bool
    unique_fills: tuple[FillEvidence, ...]
    issues: tuple[str, ...]


def audit_orders(
    orders: Sequence[OrderEvidence],
    fills: Sequence[FillEvidence],
    *,
    as_of_ms: int,
) -> EvidenceAudit:
    """No inferred fills; order cumulative qty and unique executions must reconcile."""
    known: dict[tuple[Scope, str], OrderEvidence] = {}
    for order in orders:
        if order.observed_ms > as_of_ms:
            continue
        if order.decision_ms < 0 or order.decision_ms > order.observed_ms:
            raise CausalityError("order decision time")
        for value in (order.requested_qty, order.intent_qty, order.limit_price):
            positive(value, "order field")
        if (
            not order.cumulative_qty.is_finite()
            or not ZERO <= order.cumulative_qty <= order.requested_qty
        ):
            raise DataIntegrityError("order cumulative quantity")
        if order.side not in ("BUY", "SELL") or not order.intent_id or not order.order_id:
            raise DataIntegrityError("order identity")
        key = order.scope, order.order_id
        if key in known:
            raise DataIntegrityError("duplicate order snapshot")
        known[key] = order
    unique: dict[tuple[Scope, str], FillEvidence] = {}
    for fill in sorted(fills, key=lambda f: f.received_ms):
        if fill.received_ms > as_of_ms:
            continue
        if fill.exchange_ms < 0 or fill.exchange_ms > fill.received_ms:
            raise CausalityError("fill time")
        matched_order = known.get((fill.scope, fill.order_id))
        if matched_order is None:
            raise DataIntegrityError("fill order missing/scope mismatch")
        if (
            not matched_order.decision_ms <= fill.exchange_ms <= matched_order.observed_ms
            or fill.side != matched_order.side
            or not fill.trade_id
        ):
            raise DataIntegrityError("fill decision/side/identity")
        for value in (fill.qty, fill.price):
            positive(value, "fill")
        if fill.fee is None or not fill.fee.is_finite() or fill.fee_asset != "USDT":
            raise DataIntegrityError("fee unknown/unsupported quote asset")
        if (fill.side == "BUY" and fill.price > matched_order.limit_price) or (
            fill.side == "SELL" and fill.price < matched_order.limit_price
        ):
            raise DataIntegrityError("fill outside order limit")
        key = fill.scope, fill.trade_id
        if key in unique:
            previous = unique[key]
            economic_old = (
                previous.order_id,
                previous.side,
                previous.qty,
                previous.price,
                previous.fee,
                previous.fee_asset,
                previous.exchange_ms,
            )
            economic_new = (
                fill.order_id,
                fill.side,
                fill.qty,
                fill.price,
                fill.fee,
                fill.fee_asset,
                fill.exchange_ms,
            )
            if economic_old != economic_new:
                raise DataIntegrityError("conflicting trade identity")
        else:
            unique[key] = fill
    issues: list[str] = []
    intents: dict[tuple[Scope, str], tuple[Decimal, Decimal, Side]] = {}
    if not known:
        issues.append("NO_ORDER_EVIDENCE")
    for key, order in known.items():
        matched = sum((f.qty for f in unique.values() if (f.scope, f.order_id) == key), ZERO)
        if matched > order.requested_qty:
            raise DataIntegrityError("overfilled order")
        if matched != order.cumulative_qty:
            issues.append("CUMULATIVE_MISMATCH")
        if order.state not in ("FILLED", "EXPIRED", "CANCELED", "REJECTED", "NOT_PLACED"):
            issues.append("UNRESOLVED_ORDER")
        if order.state == "FILLED" and order.cumulative_qty != order.requested_qty:
            raise DataIntegrityError("false FILLED")
        if order.state in ("REJECTED", "NOT_PLACED") and matched != ZERO:
            raise DataIntegrityError("rejected order with fills")
        intent_key = order.scope, order.intent_id
        limit, amount, side = intents.get(intent_key, (order.intent_qty, ZERO, order.side))
        if limit != order.intent_qty or side != order.side:
            raise DataIntegrityError("intent contract changed")
        intents[intent_key] = limit, amount + matched, side
    if any(amount > limit for limit, amount, _ in intents.values()):
        raise DataIntegrityError("overfilled aggregate intent")
    kept = tuple(sorted(unique.values(), key=lambda f: (f.exchange_ms, f.trade_id)))
    return EvidenceAudit(as_of_ms, not issues, kept, tuple(sorted(set(issues))))


@dataclass(frozen=True)
class FundingCash:
    scope: Scope
    event_id: str
    cash_usdt: Decimal
    exchange_ms: int
    received_ms: int


@dataclass(frozen=True)
class Ledger:
    short_qty: Decimal
    average_entry: Decimal
    realized: Decimal
    fees: Decimal
    funding: Decimal
    cash: Decimal
    unrealized: Decimal
    equity: Decimal


def replay_short(
    evidence: EvidenceAudit,
    scope: Scope,
    funding: Sequence[FundingCash],
    *,
    initial_cash: Decimal,
    mark: Mark,
    as_of_ms: int,
) -> Ledger:
    """Evidence replay only, no protection, liquidation, or full portfolio risk parity."""
    if not evidence.complete:
        raise DataIntegrityError("incomplete evidence")
    if evidence.as_of_ms > as_of_ms:
        raise CausalityError("audit reused before order evidence time")
    positive(initial_cash, "initial cash")
    positive(mark.price, "valuation mark")
    if mark.symbol != scope.symbol or mark.received_ms < 0:
        raise DataIntegrityError("valuation scope/time")
    if mark.received_ms > as_of_ms:
        raise CausalityError("valuation not received")
    if as_of_ms - mark.received_ms > DEFAULT_QUOTE_POLICY.max_age_ms:
        raise StaleDataError("valuation stale")
    qty = entry = realized = fees = ZERO
    sides: dict[int, set[str]] = {}
    for fill in evidence.unique_fills:
        if fill.scope == scope:
            sides.setdefault(fill.exchange_ms, set()).add(fill.side)
    if any(len(values) > 1 for values in sides.values()):
        raise DataIntegrityError("ambiguous same-ms opening/reduction")
    for fill in evidence.unique_fills:
        if fill.scope != scope:
            continue
        if fill.received_ms > as_of_ms:
            raise CausalityError("audit reused before evidence time")
        if fill.side == "SELL":
            entry = (qty * entry + fill.qty * fill.price) / (qty + fill.qty)
            qty += fill.qty
        else:
            if fill.qty > qty:
                raise DataIntegrityError("over-reduction/position reversal")
            realized += fill.qty * (entry - fill.price)
            qty -= fill.qty
            if qty == ZERO:
                entry = ZERO
        if fill.fee is None:
            raise DataIntegrityError("fee unknown")
        fees += fill.fee
    fund: dict[str, FundingCash] = {}
    for event in sorted(funding, key=lambda f: f.received_ms):
        if event.scope != scope or event.received_ms > as_of_ms:
            continue
        if event.exchange_ms < 0 or event.exchange_ms > event.received_ms:
            raise CausalityError("funding evidence time")
        if not event.cash_usdt.is_finite() or not event.event_id:
            raise DataIntegrityError("funding evidence invalid")
        if event.event_id in fund:
            prior = fund[event.event_id]
            if (prior.cash_usdt, prior.exchange_ms) != (event.cash_usdt, event.exchange_ms):
                raise DataIntegrityError("funding duplicate conflict")
        else:
            fund[event.event_id] = event
    fund_cash = sum((f.cash_usdt for f in fund.values()), ZERO)
    cash = initial_cash + realized - fees + fund_cash
    upnl = qty * (entry - mark.price)
    return Ledger(qty, entry, realized, fees, fund_cash, cash, upnl, cash + upnl)
