"""Frozen explanatory AG/RSI accounting. No operating imports or I/O."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Any, Literal

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.core.money import floor_to_step
from auto_trading.research.wallet_grid_20261013 import (
    BAND,
    CAP,
    DAY,
    FUT_STEP,
    H4,
    INITIAL,
    MIN_NOTIONAL,
    MINUTE,
    ONE,
    SCENARIOS,
    SPOT_STEP,
    SYMBOLS,
    ZERO,
    Bar,
    Block,
    Funding,
    Minute,
    Scenario,
    valid_bar,
)

D = Decimal
Mode = Literal["BASE", "SHORT_1X", "SHORT_MAX"]
__all__ = [
    "DAY",
    "H4",
    "INITIAL",
    "MINUTE",
    "SCENARIOS",
    "SYMBOLS",
    "Bar",
    "Block",
    "Funding",
    "Minute",
]


@dataclass(frozen=True)
class Case:
    exposure: int
    mode: Mode
    scenario: Scenario

    @property
    def short_leverage(self) -> Decimal:
        return D(self.exposure if self.mode == "SHORT_MAX" else 1)

    @property
    def key(self) -> str:
        return f"AG{self.exposure}:{self.mode}:{self.scenario.name}"


def cases() -> tuple[Case, ...]:
    modes: tuple[Mode, ...] = ("BASE", "SHORT_1X", "SHORT_MAX")
    return tuple(Case(e, m, s) for s in SCENARIOS for e in (3, 4) for m in modes)


def split_weights(
    weights: tuple[Decimal, Decimal], exposure: int
) -> tuple[list[Decimal], list[Decimal]]:
    """Decimal equivalent of the unchanged registered AG allocation equation."""
    total, lev = sum(weights, ZERO), D(exposure)
    scale = (
        ONE if total == ZERO else max(ZERO, min(ONE, (lev - lev * total) / ((lev - ONE) * total)))
    )
    spot = [scale * w for w in weights]
    return spot, [lev * w - spot[i] for i, w in enumerate(weights)]


@dataclass
class Position:
    side: int
    qty: Decimal
    entry: Decimal
    raw_entry: Decimal
    entry_ms: int
    stop: Decimal
    opened_qty: Decimal
    leverage: Decimal
    gross: Decimal = ZERO
    fee: Decimal = ZERO
    slip: Decimal = ZERO
    funding: Decimal = ZERO


@dataclass
class Order:
    kind: Literal["SPOT", "FUTURE", "SHORT"]
    wanted: Decimal
    reference: Decimal
    expires_ms: int
    reason: str
    attempts: int = 0


@dataclass
class Account:
    case: Case
    cash: Decimal = INITIAL
    spot: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    positions: dict[int, Position] = field(default_factory=dict)
    orders: dict[tuple[int, str], Order] = field(default_factory=dict)
    goals: tuple[Decimal, Decimal] = (ZERO, ZERO)
    targets_known: bool = False
    targets_updated_ms: int | None = None
    stop_refs: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    rates: list[Decimal | None] = field(default_factory=lambda: [None, None])
    flat_ack: list[int] = field(default_factory=lambda: [-DAY, -DAY])
    busy_ms: int = -1
    cooldown_ms: int = -1
    day_start: Decimal = INITIAL
    peak: Decimal = INITIAL
    short_halted: bool = False
    stopped: bool = False
    streak: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    spot_flow: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    gross: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    fees: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    slips: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    funding: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    turnover: Decimal = ZERO
    mdd: Decimal = ZERO
    dd_bound: Decimal = ZERO
    exposure_sum: Decimal = ZERO
    exposure_max: Decimal = ZERO
    fills: list[dict[str, Any]] = field(default_factory=list)
    funding_events: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    sizing: list[dict[str, Any]] = field(default_factory=list)
    returns: list[dict[str, Any]] = field(default_factory=list)
    returning: dict[int, dict[str, Any]] = field(default_factory=dict)

    def unrealized(self, perp: tuple[Bar, Bar], field_name: str = "o") -> Decimal:
        return sum(
            (
                p.side * p.qty * (getattr(perp[i], field_name) - p.entry)
                for i, p in self.positions.items()
            ),
            ZERO,
        )

    def equity(
        self, spot: tuple[Bar, Bar], perp: tuple[Bar, Bar], field_name: str = "o"
    ) -> Decimal:
        return (
            self.cash
            + sum((q * getattr(spot[i], field_name) for i, q in enumerate(self.spot)), ZERO)
            + self.unrealized(perp, field_name)
        )

    def margins(self, perp: tuple[Bar, Bar]) -> tuple[Decimal, Decimal]:
        long = sum(
            (
                p.qty * perp[i].o / D(self.case.exposure)
                for i, p in self.positions.items()
                if p.side > 0
            ),
            ZERO,
        )
        short = sum(
            (p.qty * perp[i].o / p.leverage for i, p in self.positions.items() if p.side < 0), ZERO
        )
        return long, short


def _new_allowed(a: Account, m: Minute, *, short: bool) -> bool:
    equity = a.equity(m.spot, m.perp)
    if equity <= a.peak * D("0.75"):
        a.short_halted = True
    if (
        not a.targets_known
        or a.stopped
        or equity <= ZERO
        or equity <= a.day_start * D("0.98")
        or not all(b.observed for b in m.spot)
    ):
        a.counts["new_risk_or_stale_veto"] += 1
        return False
    if short and (a.short_halted or m.at_ms < a.cooldown_ms):
        a.counts["short_dd25_or_streak_veto"] += 1
        return False
    return True


def short_size(a: Account, i: int, m: Minute, price: Decimal) -> tuple[Decimal, dict[str, Any]]:
    """Post-cost margin/risk checks. Leverage is not a PnL multiplier."""
    equity, lev = a.equity(m.spot, m.perp), a.case.short_leverage
    cost = D(a.case.scenario.cost)
    fee_pct = D("0.0005") * cost
    raw = m.perp[i].o
    entry_loss_unit = raw - price + price * fee_pct
    long_margin, short_margin = a.margins(m.perp)
    collateral = a.cash + min(ZERO, a.unrealized(m.perp))
    free = max(ZERO, collateral - long_margin - D("1.5") * short_margin)
    rate = a.rates[i]
    if rate is None:
        return ZERO, {"reason": "FUNDING_UNKNOWN"}
    stop_mult = D("1.03") * (ONE + D("0.00245") * cost)
    risk_pct = max(
        D("0.03") + D("0.0039") * cost + max(ZERO, -rate) * 6,
        raw / price - ONE + stop_mult - ONE + fee_pct * (ONE + stop_mult) + max(ZERO, -rate) * 6,
    )
    gross_now = sum((p.qty * m.perp[j].o for j, p in a.positions.items()), ZERO)
    portfolio_lev = D(a.case.exposure)
    limits = {
        "REQUEST": equity * D("0.10") * lev / price,
        "MARGIN10": max(ZERO, D("0.10") * equity - short_margin)
        / (raw / lev + D("0.10") * entry_loss_unit),
        "RISK1": D("0.01") * equity / (price * risk_pct + D("0.01") * entry_loss_unit),
        "FREE_BUFFER": free / (D("1.5") * raw / lev + entry_loss_unit),
        "TOTAL_FUTURES_CAP": max(ZERO, portfolio_lev * collateral - gross_now)
        / (raw + portfolio_lev * entry_loss_unit),
    }
    binding = min(limits, key=lambda k: limits[k])
    quantity = floor_to_step(min(limits.values()), FUT_STEP)
    before_liquidity = quantity
    quantity = floor_to_step(
        min(quantity, m.previous_perp[i].volume * a.case.scenario.participation), FUT_STEP
    )
    if m.perp[i].volume == ZERO:
        quantity = ZERO
    after_equity = equity - quantity * entry_loss_unit
    return quantity, {
        "at_ms": m.at_ms,
        "symbol": SYMBOLS[i],
        "leverage": lev,
        "equity_before": equity,
        "equity_after": after_equity,
        "request_notional": equity * D("0.10") * lev,
        "notional": quantity * price,
        "mark_notional": quantity * raw,
        "margin": quantity * raw / lev,
        "margin_total_after": short_margin + quantity * raw / lev,
        "risk": quantity * price * risk_pct,
        "binding": binding,
        "liquidity_clipped": quantity < before_liquidity,
        "limits_qty": limits,
    }


def _finish(a: Account, i: int, at_ms: int, reason: str, emergency: bool) -> None:
    p = a.positions.pop(i)
    net = p.gross + p.funding - p.fee - p.slip
    a.trades.append(
        {
            "symbol": SYMBOLS[i],
            "side": "SHORT" if p.side < 0 else "LONG",
            "entry_ms": p.entry_ms,
            "exit_ms": at_ms,
            "qty": p.opened_qty,
            "net": net,
            "gross": p.gross,
            "fee": p.fee,
            "slip": p.slip,
            "funding": p.funding,
            "reason": reason,
            "leverage": p.leverage,
        }
    )
    ack = at_ms + (a.case.scenario.ack_min + int(emergency)) * MINUTE
    a.flat_ack[i] = ack
    a.busy_ms = max(a.busy_ms, ack)
    if p.side < 0:
        a.counts["short_closed"] += 1
        a.cooldown_ms = max(a.cooldown_ms, ack + 60 * MINUTE)
        a.streak = a.streak + 1 if net < ZERO else 0
        if a.streak >= 4:
            a.cooldown_ms = max(a.cooldown_ms, ack + 240 * MINUTE)
            a.streak = 0
        if i in a.returning:
            a.returning[i]["flat_ack_ms"] = ack


def future_fill(
    a: Account,
    i: int,
    delta: Decimal,
    raw: Decimal,
    at_ms: int,
    reason: str,
    *,
    emergency: bool = False,
    sizing: dict[str, Any] | None = None,
) -> None:
    """Signed contracts: cash changes only by closed PnL, fees and funding."""
    if delta == ZERO:
        return
    p = a.positions.get(i)
    side = 1 if delta > ZERO else -1
    slip_pct = D("0.00045") * a.case.scenario.cost
    if emergency:
        slip_pct += (
            D("0.01") if p is not None and p.side > 0 else D("0.002")
        ) * a.case.scenario.cost
    price = raw * (ONE + D(side) * slip_pct)
    qty = abs(delta)
    fee = qty * price * D("0.0005") * a.case.scenario.cost
    slip = qty * abs(price - raw)
    cash_delta = -fee
    market_gross = ZERO
    if p is not None and p.side != side:
        if qty > p.qty:
            raise DataIntegrityError("direction reversal without flat confirmation")
        market_gross = p.side * qty * (raw - p.raw_entry)
        cash_delta += p.side * qty * (price - p.entry)
        p.gross += market_gross
        p.qty -= qty
        p.fee += fee
        p.slip += slip
    elif p is not None:
        total = p.qty + qty
        p.entry = (p.entry * p.qty + price * qty) / total
        p.raw_entry = (p.raw_entry * p.qty + raw * qty) / total
        p.qty = total
        p.opened_qty += qty
        p.fee += fee
        p.slip += slip
    else:
        stop = price * D("1.03") if side < 0 else a.stop_refs[i]
        a.positions[i] = Position(
            side,
            qty,
            price,
            raw,
            at_ms,
            stop,
            qty,
            a.case.short_leverage if side < 0 else D(a.case.exposure),
            fee=fee,
            slip=slip,
        )
        if side < 0:
            a.counts["short_opened"] += 1
    a.cash += cash_delta
    a.gross[i] += market_gross
    a.fees[i] += fee
    a.slips[i] += slip
    a.turnover += qty * price
    a.fills.append(
        {
            "at_ms": at_ms,
            "symbol": SYMBOLS[i],
            "asset": "FUTURE",
            "delta": delta,
            "raw_price": raw,
            "price": price,
            "fee": fee,
            "slip": slip,
            "market_gross": market_gross,
            "cash_delta": cash_delta,
            "reason": reason,
            "sizing": sizing,
        }
    )
    a.busy_ms = max(a.busy_ms, at_ms + a.case.scenario.ack_min * MINUTE)
    if p is not None and p.qty == ZERO:
        _finish(a, i, at_ms, reason, emergency)


def spot_fill(a: Account, i: int, delta: Decimal, raw: Decimal, at_ms: int, reason: str) -> None:
    price = raw * (ONE + (ONE if delta > ZERO else -ONE) * D("0.0005") * a.case.scenario.cost)
    fee = abs(delta) * price * D("0.001") * a.case.scenario.cost
    slip = abs(delta) * abs(price - raw)
    a.cash -= delta * price + fee
    a.spot[i] += delta
    a.spot_flow[i] -= delta * raw
    a.fees[i] += fee
    a.slips[i] += slip
    a.turnover += abs(delta) * price
    a.fills.append(
        {
            "at_ms": at_ms,
            "symbol": SYMBOLS[i],
            "asset": "SPOT",
            "delta": delta,
            "raw_price": raw,
            "price": price,
            "fee": fee,
            "slip": slip,
            "cash_delta": -delta * price - fee,
            "reason": reason,
        }
    )
    a.busy_ms = max(a.busy_ms, at_ms + a.case.scenario.ack_min * MINUTE)
    if delta > ZERO and i in a.returning:
        event = a.returning.pop(i)
        event.update(first_buy_ms=at_ms, bought_qty=delta, status="FIRST_BUY")
        a.returns.append(event)


def _fund(
    a: Account, i: int, event: Funding, *, uncertain_stop: bool, update_rate: bool = True
) -> None:
    if update_rate:
        a.rates[i] = event.rate
    p = a.positions.get(i)
    if p is None:
        return
    amount = -D(p.side) * p.qty * event.mark * event.rate
    if uncertain_stop and amount > ZERO:
        a.counts["funding_credit_omitted_at_stop"] += 1
        return
    a.cash += amount
    a.funding[i] += amount
    p.funding += amount
    a.funding_events.append(
        {
            "at_ms": event.at_ms,
            "symbol": SYMBOLS[i],
            "signed_qty": D(p.side) * p.qty,
            "rate": event.rate,
            "mark": event.mark,
            "amount": amount,
            "exact_mark": event.exact_mark,
        }
    )


def _attempt(a: Account, i: int, order: Order, m: Minute) -> bool:
    if m.at_ms < a.busy_ms:
        return False
    if order.attempts >= 3 or m.at_ms >= order.expires_ms:
        a.counts["attempt_expired"] += 1
        return True
    p = a.positions.get(i)
    current = a.spot[i] if order.kind == "SPOT" else ZERO if p is None else D(p.side) * p.qty
    if order.kind == "SHORT":
        if p is not None or a.goals[i] > ZERO or a.spot[i] > ZERO:
            a.counts["short_overlap_veto"] += 1
            return True
        if not _new_allowed(a, m, short=True):
            return True
        delta = -ONE
    else:
        delta = order.wanted - current
    if delta == ZERO:
        return True
    opening = (order.kind == "SPOT" and delta > ZERO) or (
        order.kind == "FUTURE" and delta > ZERO and current >= ZERO
    )
    if opening:
        if p is not None and p.side < 0:
            return True
        if m.at_ms < a.flat_ack[i] + 60 * MINUTE or not _new_allowed(a, m, short=False):
            a.counts["long_cooldown_or_risk_veto"] += 1
            return True
    bar = m.spot[i] if order.kind == "SPOT" else m.perp[i]
    previous = m.previous_spot[i] if order.kind == "SPOT" else m.previous_perp[i]
    if not bar.observed:
        a.counts["venue_closed_veto"] += 1
        return True
    order.attempts += 1
    slip = D("0.0005") if order.kind == "SPOT" else D("0.00045")
    price = bar.o * (ONE + (ONE if delta > ZERO else -ONE) * slip * a.case.scenario.cost)
    if (delta > ZERO and price > order.reference * (ONE + CAP)) or (
        delta < ZERO and price < order.reference * (ONE - CAP)
    ):
        a.counts["limit_miss"] += 1
        return order.attempts >= 3
    step = SPOT_STEP if order.kind == "SPOT" else FUT_STEP
    fee_pct = (D("0.001") if order.kind == "SPOT" else D("0.0005")) * a.case.scenario.cost
    sizing = None
    if order.kind == "SHORT":
        qty, sizing = short_size(a, i, m, price)
        a.sizing.append(sizing)
        if sizing.get("binding"):
            a.counts[f"short_size:{sizing['binding']}"] += 1
    else:
        qty = abs(delta)
        long_margin, short_margin = a.margins(m.perp)
        collateral = a.cash + min(ZERO, a.unrealized(m.perp))
        if opening and order.kind == "SPOT":
            qty = min(
                qty,
                max(ZERO, collateral - long_margin - D("1.5") * short_margin)
                / (price * (ONE + fee_pct)),
            )
        elif opening:
            if a.stop_refs[i] <= ZERO or a.stop_refs[i] >= price:
                a.counts["invalid_long_stop"] += 1
                return True
            gross = sum((x.qty * m.perp[j].o for j, x in a.positions.items()), ZERO)
            lev = D(a.case.exposure)
            loss_unit = price - bar.o + price * fee_pct
            qty = min(qty, max(ZERO, lev * collateral - gross) / (bar.o + lev * loss_unit))
        qty = floor_to_step(min(qty, previous.volume * a.case.scenario.participation), step)
        if bar.volume == ZERO:
            qty = ZERO
    if qty <= ZERO or qty * price < MIN_NOTIONAL:
        a.counts["cash_liquidity_min_veto"] += 1
        return order.attempts >= 3
    delta = qty * (ONE if delta > ZERO else -ONE)
    if order.kind == "SPOT":
        spot_fill(a, i, delta, bar.o, m.at_ms, order.reason)
    else:
        future_fill(a, i, delta, bar.o, m.at_ms, order.reason, sizing=sizing)
    if order.kind == "SHORT":
        return True
    new_position = a.positions.get(i)
    new_current = (
        a.spot[i]
        if order.kind == "SPOT"
        else ZERO
        if new_position is None
        else D(new_position.side) * new_position.qty
    )
    return new_current == order.wanted or order.attempts >= 3


def _begin(a: Account, b: Block, end_ms: int) -> None:
    a.orders.clear()
    if b.targets is not None:
        if b.target_as_of_ms > b.at_ms or any(w < ZERO or w > D("0.5") for w in b.targets):
            raise CausalityError("future/invalid AG targets")
        a.goals = b.targets
        a.targets_updated_ms = b.at_ms
        a.counts["daily_target_updates"] += 1
    # None is an absent update event, not a missing previously confirmed daily goal.
    # Initial absence and a missing next daily update still fail closed.
    a.targets_known = (
        a.targets_updated_ms is not None
        and a.targets_updated_ms <= b.at_ms < a.targets_updated_ms + DAY
    )
    if b.targets is None and a.targets_known:
        a.counts["daily_target_carried_blocks"] += 1
    elif not a.targets_known:
        a.counts["daily_target_unknown_or_expired_blocks"] += 1
    equity = a.equity(b.spot, b.perp)
    if b.at_ms % DAY == 0:
        a.day_start = equity
        a.stop_refs = [bar.c * D("0.8") for bar in b.previous_perp]
        for i, held in a.positions.items():
            if held.side > 0:
                held.stop = a.stop_refs[i]
    if equity <= INITIAL * D("0.30"):
        a.stopped = True
    if equity <= a.peak * D("0.75"):
        a.short_halted = True
    spot_w, fut_w = split_weights(a.goals, a.case.exposure)
    for i in range(2):
        p = a.positions.get(i)
        if p is not None and p.side < 0:
            priority = a.goals[i] > ZERO
            if priority and i not in a.returning:
                a.returning[i] = {"symbol": SYMBOLS[i], "requested_ms": b.at_ms}
            if priority or a.stopped or b.at_ms >= p.entry_ms + 48 * 60 * MINUTE:
                reason = "LONG_PRIORITY" if priority else "ACCOUNT_STOP" if a.stopped else "48H"
                a.orders[i, "FUTURE"] = Order(
                    "FUTURE", ZERO, b.previous_perp[i].c, b.at_ms + H4, reason
                )
            continue
        if i in a.returning and a.goals[i] == ZERO:
            event = a.returning.pop(i)
            event["status"] = "GOAL_ZERO"
            a.returns.append(event)
        current = (
            (a.spot[i] * b.spot[i].o + (p.qty * b.perp[i].o if p else ZERO)) / equity
            if equity > ZERO
            else ZERO
        )
        wanted = (spot_w[i] + fut_w[i]) if not a.stopped else ZERO
        change = abs(wanted - current) >= BAND * a.case.exposure or (
            wanted == ZERO and current != ZERO
        )
        if change:
            sq = (
                floor_to_step(spot_w[i] * max(equity, ZERO) / b.spot[i].o, SPOT_STEP)
                if not a.stopped
                else ZERO
            )
            fq = (
                floor_to_step(fut_w[i] * max(equity, ZERO) / b.perp[i].o, FUT_STEP)
                if not a.stopped
                else ZERO
            )
            reason = "ACCOUNT_STOP" if a.stopped else "AG_REBALANCE"
            if sq != a.spot[i]:
                a.orders[i, "SPOT"] = Order("SPOT", sq, b.previous_spot[i].c, b.at_ms + H4, reason)
            if fq != (p.qty if p else ZERO):
                a.orders[i, "FUTURE"] = Order(
                    "FUTURE", fq, b.previous_perp[i].c, b.at_ms + H4, reason
                )
        if a.case.mode != "BASE" and b.signals[i]:
            a.counts["short_signals"] += 1
            if a.goals[i] > ZERO:
                a.counts["signal_long_goal_veto"] += 1
            elif a.spot[i] > ZERO:
                a.counts["signal_spot_residue_veto"] += 1
            elif p is not None:
                a.counts["signal_futures_overlap_veto"] += 1
            elif b.at_ms >= end_ms - 144 * 60 * MINUTE:
                a.counts["signal_cutoff_veto"] += 1
            else:
                a.orders[i, "SHORT"] = Order(
                    "SHORT", ZERO, b.previous_perp[i].c, b.at_ms + H4, "OPEN_SHORT"
                )
    # Existing AG cap also requires risk reduction after adverse moves, not just at entry.
    gross = sum((p.qty * b.perp[i].o for i, p in a.positions.items()), ZERO)
    wallet = a.cash + a.unrealized(b.perp)
    lev = D(a.case.exposure)
    if gross > lev * max(wallet, ZERO):
        cost = D("0.00095") * a.case.scenario.cost
        scale = max(ZERO, min(ONE, lev * (wallet - gross * cost) / (gross * (ONE - lev * cost))))
        for i, p in a.positions.items():
            wanted = D(p.side) * floor_to_step(p.qty * scale, FUT_STEP)
            a.orders[i, "FUTURE"] = Order(
                "FUTURE", wanted, b.previous_perp[i].c, b.at_ms + H4, "DELEVERAGE"
            )
        for key in list(a.orders):
            if a.orders[key].kind == "SHORT" or (
                a.orders[key].kind == "SPOT" and a.orders[key].wanted > a.spot[key[0]]
            ):
                a.orders.pop(key)
        a.counts["deleverage_requested_blocks"] += 1


def _needs_minutes(a: Account, b: Block) -> bool:
    return bool(a.orders) or any(
        p.side < 0 or b.perp[i].low <= p.stop for i, p in a.positions.items()
    )


def _minute(a: Account, m: Minute, groups: tuple[tuple[Funding, ...], tuple[Funding, ...]]) -> None:
    stopping = {
        i
        for i, p in a.positions.items()
        if m.perp[i].observed and (m.perp[i].h >= p.stop if p.side < 0 else m.perp[i].low <= p.stop)
    }
    for i, events in enumerate(groups):
        for event in events:
            if event.at_ms == m.at_ms:
                _fund(a, i, event, uncertain_stop=i in stopping)
            elif i in stopping:
                _fund(a, i, event, uncertain_stop=True, update_rate=False)
    for i in sorted(stopping):
        p = a.positions[i]
        raw = max(m.perp[i].o, p.stop) if p.side < 0 else min(m.perp[i].o, p.stop)
        future_fill(
            a,
            i,
            -D(p.side) * p.qty,
            raw,
            m.at_ms,
            "SHORT_STOP" if p.side < 0 else "LONG_STOP",
            emergency=True,
        )
        a.orders.pop((i, "FUTURE"), None)
        a.orders.pop((i, "SHORT"), None)
    for i, p in list(a.positions.items()):
        if (
            p.side < 0
            and m.at_ms >= p.entry_ms + 48 * 60 * MINUTE
            and (i, "FUTURE") not in a.orders
        ):
            a.orders[i, "FUTURE"] = Order("FUTURE", ZERO, m.previous_perp[i].c, m.at_ms + H4, "48H")

    # Reductions first: never use futures collateral twice for the same decision.
    def priority(item: tuple[tuple[int, str], Order]) -> tuple[int, int, str]:
        (i, kind), order = item
        p = a.positions.get(i)
        q = a.spot[i] if kind == "SPOT" else ZERO if p is None else D(p.side) * p.qty
        reducing = order.kind != "SHORT" and abs(order.wanted) < abs(q)
        return (0 if reducing else 1, i, kind)

    for key, order in sorted(a.orders.items(), key=priority):
        if key in a.orders and _attempt(a, key[0], order, m):
            a.orders.pop(key, None)
    # Newly opened contracts are protected in their entry minute too.
    new_stopping = {
        i
        for i, held in a.positions.items()
        if m.perp[i].observed
        and (m.perp[i].h >= held.stop if held.side < 0 else m.perp[i].low <= held.stop)
    }
    for i, events in enumerate(groups):
        for event in events:
            if event.at_ms > m.at_ms and i not in stopping:
                _fund(a, i, event, uncertain_stop=i in new_stopping, update_rate=False)
    for i in sorted(new_stopping):
        held = a.positions[i]
        raw = max(m.perp[i].o, held.stop) if held.side < 0 else min(m.perp[i].o, held.stop)
        future_fill(
            a,
            i,
            -D(held.side) * held.qty,
            raw,
            m.at_ms,
            "SHORT_STOP" if held.side < 0 else "LONG_STOP",
            emergency=True,
        )
        a.orders.pop((i, "FUTURE"), None)
        a.orders.pop((i, "SHORT"), None)
    for i, events in enumerate(groups):
        for event in events:
            if event.at_ms > m.at_ms:
                a.rates[i] = event.rate


def _coin_net(a: Account, b: Block) -> dict[str, Decimal]:
    values = {}
    for i, symbol in enumerate(SYMBOLS):
        p = a.positions.get(i)
        unreal = D(p.side) * p.qty * (b.perp[i].c - p.raw_entry) if p else ZERO
        values[symbol] = (
            a.spot_flow[i]
            + a.spot[i] * b.spot[i].c
            + a.gross[i]
            + unreal
            + a.funding[i]
            - a.fees[i]
            - a.slips[i]
        )
    return values


def _checkpoint(a: Account, b: Block) -> None:
    equity = a.equity(b.spot, b.perp, "c")
    a.peak = max(a.peak, equity)
    a.mdd = max(a.mdd, ONE - equity / a.peak)
    if equity <= a.peak * D("0.75"):
        a.short_halted = True
    if equity <= INITIAL * D("0.30"):
        a.stopped = True
    exposure = (
        (
            sum((a.spot[i] * b.spot[i].c for i in range(2)), ZERO)
            + sum((p.qty * b.perp[i].c for i, p in a.positions.items()), ZERO)
        )
        / equity
        if equity > ZERO
        else ZERO
    )
    a.exposure_sum += exposure
    a.exposure_max = max(a.exposure_max, exposure)
    adverse = (
        a.cash
        + sum((a.spot[i] * b.spot[i].low for i in range(2)), ZERO)
        + sum(
            (
                D(p.side) * p.qty * ((b.perp[i].low if p.side > 0 else b.perp[i].h) - p.entry)
                for i, p in a.positions.items()
            ),
            ZERO,
        )
    )
    a.dd_bound = max(a.dd_bound, ONE - adverse / a.peak)
    long_margin, short_margin = a.margins(b.perp)
    collateral = a.cash + min(ZERO, a.unrealized(b.perp, "c"))
    if collateral < long_margin + D("1.5") * short_margin:
        a.counts["collateral_buffer_warning_points"] += 1
    gross = sum((p.qty * b.perp[i].c for i, p in a.positions.items()), ZERO)
    if gross > D(a.case.exposure) * max(ZERO, a.cash + a.unrealized(b.perp, "c")):
        a.counts["post_move_leverage_warning_points"] += 1
    if gross > ZERO and a.cash + a.unrealized(b.perp, "c") <= gross * D("0.005"):
        a.counts["maintenance_proxy_warning_points"] += 1
    a.trace.append(
        {
            "at_ms": b.at_ms + H4,
            "equity": equity,
            "cash": a.cash,
            "exposure": exposure,
            "coin_net": _coin_net(a, b),
            "short_halted": a.short_halted,
            "account_stopped": a.stopped,
        }
    )


def simulate(
    blocks: Iterable[Block], *, end_ms: int, selected: tuple[Case, ...] | None = None
) -> dict[str, dict[str, Any]]:
    chosen = cases() if selected is None else selected
    if (
        not chosen
        or any(c not in cases() for c in chosen)
        or len({c.key for c in chosen}) != len(chosen)
    ):
        raise DataIntegrityError("unregistered AG cases")
    accounts = [Account(c) for c in chosen]
    previous_ms: int | None = None
    last: Block | None = None
    for b in blocks:
        if b.at_ms + H4 > end_ms or (previous_ms is not None and b.at_ms != previous_ms + H4):
            raise DataIntegrityError("AG cutoff/gap")
        if b.signal_as_of_ms > b.at_ms:
            raise CausalityError("future AG short signal")
        for bar in (*b.spot, *b.perp, *b.previous_spot, *b.previous_perp):
            valid_bar(bar)
        for group in b.funding:
            if any(x.at_ms >= y.at_ms for x, y in pairwise(group)) or any(
                not b.at_ms <= x.at_ms < b.at_ms + H4
                or not x.rate.is_finite()
                or not x.mark.is_finite()
                or x.mark <= ZERO
                for x in group
            ):
                raise DataIntegrityError("AG funding schema/order")
        last, previous_ms = b, b.at_ms
        for a in accounts:
            _begin(a, b, end_ms)
        active = [a for a in accounts if _needs_minutes(a, b)]
        for a in accounts:
            if a not in active:
                for i, group in enumerate(b.funding):
                    for event in group:
                        _fund(a, i, event, uncertain_stop=False)
        by_minute: dict[int, list[list[Funding]]] = {}
        for i, group in enumerate(b.funding):
            for event in group:
                by_minute.setdefault(event.at_ms // MINUTE * MINUTE, [[], []])[i].append(event)
        if active:
            for n in range(H4 // MINUTE):
                minute = b.minutes.get(n)
                if minute.at_ms != b.at_ms + n * MINUTE or minute.previous_as_of_ms != minute.at_ms:
                    raise CausalityError("AG minute causality/alignment")
                events = by_minute.get(minute.at_ms, [[], []])
                for a in active:
                    _minute(a, minute, (tuple(events[0]), tuple(events[1])))
        for a in accounts:
            _checkpoint(a, b)
    if last is None or last.at_ms + H4 != end_ms:
        raise DataIntegrityError("empty/incomplete AG replay")
    result = {}
    for a in accounts:
        coin = _coin_net(a, last)
        reserves = {
            s: a.spot[i] * last.spot[i].c * D("0.0015") * a.case.scenario.cost
            + (
                a.positions[i].qty * last.perp[i].c * D("0.00095") * a.case.scenario.cost
                if i in a.positions
                else ZERO
            )
            for i, s in enumerate(SYMBOLS)
        }
        reserve = sum(reserves.values(), ZERO)
        equity = a.trace[-1]["equity"]
        net = equity - INITIAL - reserve
        residual = net - (sum(coin.values(), ZERO) - reserve)
        if abs(residual) > D("0.0000000001"):
            raise DataIntegrityError("AG cash/accounting residual")
        for return_event in a.returning.values():
            return_event["status"] = "END_UNRESOLVED"
            a.returns.append(return_event)
        result[a.case.key] = {
            "case": {
                "exposure": a.case.exposure,
                "mode": a.case.mode,
                "short_leverage": a.case.short_leverage,
                "scenario": a.case.scenario.name,
            },
            "net": net,
            "terminal_equity": equity,
            "terminal_cost_reserve": reserve,
            "coin_reserve": reserves,
            "coin_net_before_reserve": coin,
            "residual": residual,
            "fees": sum(a.fees, ZERO),
            "slips": sum(a.slips, ZERO),
            "funding": sum(a.funding, ZERO),
            "turnover": a.turnover,
            "mdd": a.mdd,
            "dd_bound": a.dd_bound,
            "mean_exposure": a.exposure_sum / len(a.trace),
            "max_exposure": a.exposure_max,
            "terminal_cash": a.cash,
            "terminal_spot_qty": a.spot,
            "terminal_positions": a.positions,
            "terminal_spot_prices": tuple(x.c for x in last.spot),
            "terminal_perp_prices": tuple(x.c for x in last.perp),
            "counts": dict(a.counts),
            "trace": a.trace,
            "fills": a.fills,
            "trades": a.trades,
            "funding_events": a.funding_events,
            "sizing": a.sizing,
            "returns": a.returns,
            "operating_risk_parity": "NOT_VERIFIED",
        }
    return result
