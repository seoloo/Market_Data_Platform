"""Approved explanatory switching replay. Pure accounting, never a live execution path."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.core.money import floor_to_step
from auto_trading.research.signal_switching_20261014 import (
    Action,
    Direction,
    Evidence,
    Policy,
    Signal,
    State,
    decide_switch,
)
from auto_trading.research.wallet_grid_20261013 import Bar, Funding, Scenario, valid_bar

D = Decimal
ZERO = D(0)
ONE = D(1)
MINUTE = 60_000
H4 = 240 * MINUTE


class Market(StrEnum):
    CRYPTO = "CRYPTO"
    STOCK = "STOCK"


class Mode(StrEnum):
    FULL = "LONG_ONLY_FULL"
    LONG = "LONG_ONLY_MATCHED"
    SHORT = "SHORT_ONLY"
    SWITCH = "SWITCH"


@dataclass(frozen=True)
class Config:
    initial_cash: Decimal = Decimal("10000")
    cap_pct: Decimal = Decimal("0.003")
    drawdown_pct: Decimal = Decimal("0.25")
    daily_loss_pct: Decimal = Decimal("0.02")
    short_risk_pct: Decimal = Decimal("0.01")
    short_margin_pct: Decimal = Decimal("0.10")
    buffer: Decimal = Decimal("1.5")
    short_stop_pct: Decimal = Decimal("0.03")
    spot_fee_pct: Decimal = Decimal("0.001")
    spot_slip_pct: Decimal = Decimal("0.0005")
    future_fee_pct: Decimal = Decimal("0.0005")
    future_slip_pct: Decimal = Decimal("0.00045")
    stock_fee_pct: Decimal = Decimal("0.0025")
    stock_slip_pct: Decimal = Decimal("0.001")
    future_stop_slip_pct: Decimal = Decimal("0.002")
    min_notional: Decimal = Decimal("5")
    spot_step: Decimal = Decimal("0.000001")
    future_step: Decimal = Decimal("0.001")
    band_pct: Decimal = Decimal("0.05")
    short_hold_ms: int = 48 * 60 * MINUTE
    wait_ms: int = 60 * MINUTE
    loss_streak: int = 4
    streak_wait_ms: int = 240 * MINUTE
    max_attempts: int = 3
    funding_stress_count: int = 6
    tax_pct: Decimal = Decimal("0.22")
    tax_exempt_usd: Decimal = Decimal("1786")


@dataclass(frozen=True)
class Quote:
    asset: str
    long: Bar
    short: Bar
    previous_long: Bar
    previous_short: Bar

    def bar(self, direction: Direction, *, previous: bool = False) -> Bar:
        if direction == Direction.LONG:
            return self.previous_long if previous else self.long
        return self.previous_short if previous else self.short


@dataclass(frozen=True)
class Frame:
    at_ms: int
    end_ms: int
    day: str
    quotes: tuple[Quote, ...]
    funding: tuple[tuple[Funding, ...], ...] = ()


@dataclass
class Position:
    direction: Direction
    qty: Decimal
    entry: Decimal
    entered_ms: int
    epoch: str
    stop: Decimal = ZERO
    realized: Decimal = ZERO
    fees: Decimal = ZERO
    funding: Decimal = ZERO
    stop_pending: bool = False
    closed_index: int = -1


@dataclass
class Order:
    action: Action
    direction: Direction
    wanted_qty: Decimal
    reference: Decimal
    intent_id: str
    reason: str
    created_ms: int
    expires_ms: int
    attempts: int = 0


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return floor_to_step(max(ZERO, value), step)


@dataclass
class Book:
    market: Market
    mode: Mode
    long_ratio: Decimal
    scenario: Scenario
    cfg: Config = field(default_factory=Config)
    cash: Decimal = Decimal("10000")
    positions: dict[str, Position] = field(default_factory=dict)
    states: dict[str, State] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    flat_ms: dict[str, int] = field(default_factory=dict)
    busy_until_ms: dict[str, int] = field(default_factory=dict)
    weights: dict[str, Decimal] = field(default_factory=dict)
    rates: dict[str, Decimal] = field(default_factory=dict)
    rate_ms: dict[str, int] = field(default_factory=dict)
    expired_epoch: dict[str, int] = field(default_factory=dict)
    target_updated_ms: int = -1
    peak: Decimal = Decimal("10000")
    day_start: Decimal = Decimal("10000")
    last_equity: Decimal = Decimal("10000")
    last_day: str = ""
    halted: bool = False
    halted_at_ms: int | None = None
    cooldown_until_ms: int = -1
    streak: int = 0
    tax_year: str = ""
    year_gain: Decimal = ZERO
    tax_reserve: Decimal = ZERO
    tax_paid: Decimal = ZERO
    fees: Decimal = ZERO
    slips: Decimal = ZERO
    funding_total: Decimal = ZERO
    turnover: Decimal = ZERO
    counts: Counter[str] = field(default_factory=Counter)
    fills: list[dict[str, Any]] = field(default_factory=list)
    funding_events: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not ZERO < self.long_ratio < ONE or self.cfg.initial_cash != D("10000"):
            raise DataIntegrityError("unregistered switching cash/ratio")

    @property
    def key(self) -> str:
        return f"{self.market}:{self.long_ratio}:{self.mode}:{self.scenario.name}"

    def is_future(self, direction: Direction) -> bool:
        return self.market == Market.CRYPTO and direction == Direction.SHORT

    def fee_slip(self, direction: Direction) -> tuple[Decimal, Decimal]:
        if self.market == Market.STOCK:
            return self.cfg.stock_fee_pct, self.cfg.stock_slip_pct
        if self.is_future(direction):
            return self.cfg.future_fee_pct, self.cfg.future_slip_pct
        return self.cfg.spot_fee_pct, self.cfg.spot_slip_pct

    def equity(self, quotes: tuple[Quote, ...], field_name: str = "o") -> Decimal:
        equity = self.cash - self.tax_reserve
        for q in quotes:
            p = self.positions.get(q.asset)
            if p is None:
                continue
            price = getattr(q.bar(p.direction), field_name)
            equity += p.qty * (p.entry - price) if self.is_future(p.direction) else p.qty * price
        return equity

    def _margin(self, quotes: tuple[Quote, ...]) -> Decimal:
        return sum(
            (
                self.positions[q.asset].qty * q.short.o
                for q in quotes
                if q.asset in self.positions and self.is_future(self.positions[q.asset].direction)
            ),
            ZERO,
        )

    def _policy(self) -> Policy:
        return Policy(self.long_ratio, ONE - self.long_ratio)

    def _budget(self, direction: Direction) -> Decimal:
        if direction == Direction.LONG:
            return ONE if self.mode == Mode.FULL else self.long_ratio
        return ONE - self.long_ratio

    def _fresh(self, asset: str, at_ms: int) -> bool:
        s = self.states.get(asset, State())
        if self.market == Market.STOCK:
            return s.confirmed_at_ms == at_ms
        lifetime = H4 if s.wanted == Direction.SHORT else 24 * 60 * MINUTE
        return 0 <= s.confirmed_at_ms <= at_ms < s.confirmed_at_ms + lifetime

    def evidence(self, quote: Quote, frame: Frame, *, include_order: bool = True) -> Evidence:
        p = self.positions.get(quote.asset)
        confirmed = self.flat_ms.get(quote.asset, 0)
        return Evidence(
            frame.at_ms,
            quote.asset,
            p.epoch
            if p
            else f"{self.key}:flat:{self.flat_ms.get(quote.asset, 0)}:{frame.at_ms // H4}",
            p.direction if p else Direction.FLAT,
            p.qty if p else ZERO,
            unresolved_order=(include_order and quote.asset in self.orders)
            or frame.at_ms < self.busy_until_ms.get(quote.asset, -1),
            flat_confirmed_at_ms=confirmed if confirmed <= frame.at_ms else None,
            balance_fresh=all(q.long.observed and q.short.observed for q in frame.quotes),
            signal_fresh=self._fresh(quote.asset, frame.at_ms),
            trading_halted=self.halted,
        )

    def begin(
        self, frame: Frame, signals: dict[str, tuple[Signal, ...]], weights: dict[str, Decimal]
    ) -> None:
        if frame.at_ms >= frame.end_ms or len(frame.funding) not in (0, len(frame.quotes)):
            raise DataIntegrityError("invalid switching frame")
        if self.last_day != frame.day:
            self.day_start = self.last_equity
            self.last_day = frame.day
        year = frame.day[:4]
        if self.market == Market.STOCK and year != self.tax_year:
            if self.cash < self.tax_reserve:
                raise DataIntegrityError("tax cash not reserved")
            self.cash -= self.tax_reserve
            self.tax_paid += self.tax_reserve
            self.tax_reserve = self.year_gain = ZERO
            self.tax_year = year
        self.weights.update(weights)
        for q in frame.quotes:
            for signal in signals.get(q.asset, ()):
                if self.mode in (Mode.FULL, Mode.LONG) and signal.direction == Direction.SHORT:
                    if self.market == Market.CRYPTO:
                        continue
                    signal = Signal(Direction.FLAT, signal.signal_id, signal.confirmed_at_ms)
                if self.mode == Mode.SHORT and signal.direction == Direction.LONG:
                    signal = Signal(Direction.FLAT, signal.signal_id, signal.confirmed_at_ms)
                plan = decide_switch(
                    self.states.get(q.asset, State()),
                    self.evidence(q, frame),
                    self._policy(),
                    signal,
                )
                self.states[q.asset] = plan.state
                self.counts[f"signal:{signal.direction}"] += 1
            state = self.states.get(q.asset, State())
            self.evaluations.append(
                {
                    "at_ms": frame.at_ms,
                    "asset": q.asset,
                    "wanted": state.wanted,
                    "signal_id": state.signal_id,
                    "conflict": state.conflict,
                    "held": self.positions[q.asset].direction
                    if q.asset in self.positions
                    else Direction.FLAT,
                }
            )

    def _guard(self, frame: Frame) -> bool:
        equity = self.equity(frame.quotes)
        if equity <= self.peak * (ONE - self.cfg.drawdown_pct):
            if not self.halted:
                self.halted_at_ms = frame.at_ms
            self.halted = True
        return (
            not self.halted
            and equity > ZERO
            and equity > self.day_start * (ONE - self.cfg.daily_loss_pct)
            and frame.at_ms >= self.cooldown_until_ms
            and all(q.long.observed and q.short.observed for q in frame.quotes)
        )

    def _size(self, q: Quote, frame: Frame, direction: Direction, price: Decimal) -> Decimal:
        cfg = self.cfg
        equity = self.equity(frame.quotes)
        fee, slip = self.fee_slip(direction)
        fee *= self.scenario.cost
        slip *= self.scenario.cost
        margin = self._margin(frame.quotes)
        held_position = self.positions.get(q.asset)
        held = held_position.qty if held_position and held_position.direction == direction else ZERO
        held_margin = held * q.short.o if self.is_future(direction) else ZERO
        free = max(ZERO, self.cash - self.tax_reserve - cfg.buffer * margin)
        nominal = equity * self._budget(direction)
        if direction == Direction.LONG:
            nominal *= self.weights.get(q.asset, ZERO)
        elif self.market == Market.CRYPTO:
            nominal /= len(frame.quotes)
            if (
                q.asset not in self.rates
                or frame.at_ms - self.rate_ms.get(q.asset, -1) > 8 * 60 * MINUTE
            ):
                self.counts["funding_unknown_veto"] += 1
                return ZERO
        if direction == Direction.SHORT:
            risk_unit = cfg.short_stop_pct + fee * 2 + slip * 2
            if self.market == Market.CRYPTO:
                risk_unit += cfg.future_stop_slip_pct * self.scenario.cost
                risk_unit += abs(self.rates[q.asset]) * cfg.funding_stress_count
                nominal = min(
                    nominal,
                    max(ZERO, equity * cfg.short_margin_pct - margin + held_margin),
                    held * price + free / cfg.buffer,
                )
            nominal = min(nominal, equity * cfg.short_risk_pct / risk_unit)
        qty = nominal / price if price > ZERO else ZERO
        if self.is_future(direction):
            entry_cost = price * fee + max(ZERO, q.short.o - price)
            room = max(ZERO, equity * cfg.short_margin_pct - margin)
            qty = min(qty, held + room / (q.short.o + cfg.short_margin_pct * entry_cost))
        else:
            qty = min(qty, held + free / (price * (ONE + fee)))
        step = cfg.future_step if self.is_future(direction) else cfg.spot_step
        return floor_step(qty, step)

    def _plan_orders(self, frame: Frame) -> None:
        new_allowed = self._guard(frame)
        for q in frame.quotes:
            p = self.positions.get(q.asset)
            state = self.states.get(q.asset, State())
            if (
                p is not None
                and self.market == Market.CRYPTO
                and p.direction == Direction.SHORT
                and frame.at_ms >= p.entered_ms + self.cfg.short_hold_ms
            ):
                p.stop_pending = True
                self.counts["short_48h_exit_requested"] += int(q.asset not in self.orders)
            if q.asset in self.orders or frame.at_ms < self.busy_until_ms.get(q.asset, -1):
                continue
            force_close = p is not None and p.stop_pending
            evidence = self.evidence(q, frame)
            if not new_allowed:
                evidence = Evidence(
                    **{**evidence.__dict__, "trading_halted": self.halted, "balance_fresh": False}
                )
            plan = decide_switch(state, evidence, self._policy(), None)
            action = Action.CLOSE if force_close else plan.action
            if action == Action.WAIT:
                # Same-direction LONG resizing uses the original T/v2 target/band.
                if (
                    p is None
                    or p.direction != Direction.LONG
                    or state.wanted != Direction.LONG
                    or self.market != Market.CRYPTO
                ):
                    continue
                target_value = (
                    self.equity(frame.quotes)
                    * self._budget(Direction.LONG)
                    * self.weights.get(q.asset, ZERO)
                )
                if abs(target_value - p.qty * q.long.o) < self.equity(
                    frame.quotes
                ) * self.cfg.band_pct * self._budget(Direction.LONG):
                    continue
                if target_value < p.qty * q.long.o:
                    action = Action.CLOSE
                elif new_allowed and self._fresh(q.asset, frame.at_ms):
                    action = Action.OPEN
                else:
                    continue
            if action == Action.OPEN and not new_allowed:
                self.counts["new_risk_veto"] += 1
                continue
            direction = p.direction if action == Action.CLOSE and p else state.wanted
            if direction == Direction.FLAT:
                continue
            price = q.bar(direction).o
            _, sizing_slip = self.fee_slip(direction)
            buy = (action == Action.OPEN) != self.is_future(direction)
            sizing_price = price * (ONE + (ONE if buy else -ONE) * sizing_slip * self.scenario.cost)
            wanted = (
                self._size(q, frame, direction, sizing_price) if action == Action.OPEN else ZERO
            )
            if (
                action == Action.CLOSE
                and p is not None
                and plan.action == Action.WAIT
                and not force_close
            ):
                wanted = floor_step(target_value / price, self.cfg.spot_step)
            reference = q.bar(direction, previous=True).c
            expires = (
                ((frame.at_ms // H4) + 1) * H4
                if self.market == Market.CRYPTO
                else frame.at_ms + 4 * 24 * 60 * MINUTE
            )
            epoch = frame.at_ms // H4
            if action == Action.OPEN and self.expired_epoch.get(q.asset) == epoch:
                continue
            intent = (
                plan.intent_id or f"{self.key}:{q.asset}:{p.epoch if p else epoch}:RESIZE:{epoch}"
            )
            self.orders[q.asset] = Order(
                action,
                direction,
                wanted,
                reference,
                intent,
                "RISK_EXIT" if force_close else plan.reason,
                frame.at_ms,
                expires,
            )

    def _fill(
        self,
        q: Quote,
        frame: Frame,
        action: Action,
        direction: Direction,
        qty: Decimal,
        raw: Decimal,
        price: Decimal,
        intent: str,
        reason: str,
        *,
        at_ms: int | None = None,
        reference: Decimal | None = None,
    ) -> None:
        if qty <= ZERO:
            return
        timestamp = frame.at_ms if at_ms is None else at_ms
        fee_pct, _ = self.fee_slip(direction)
        fee = qty * price * fee_pct * self.scenario.cost
        p = self.positions.get(q.asset)
        if action == Action.OPEN:
            if p is not None and p.direction != direction:
                raise DataIntegrityError("overlapping directions")
            if p is None:
                p = Position(direction, ZERO, ZERO, timestamp, intent)
                self.positions[q.asset] = p
            total = p.qty + qty
            p.entry = (p.qty * p.entry + qty * price) / total
            p.qty = total
            p.fees += fee
            if direction == Direction.SHORT:
                p.stop = p.entry * (
                    ONE + self.cfg.short_stop_pct
                    if self.is_future(direction)
                    else ONE - self.cfg.short_stop_pct
                )
            cash_delta = -fee if self.is_future(direction) else -qty * price - fee
        else:
            if p is None or p.direction != direction or qty > p.qty:
                raise DataIntegrityError("over-close")
            gross = (
                qty * (p.entry - price) if self.is_future(direction) else qty * (price - p.entry)
            )
            p.realized += gross
            p.fees += fee
            cash_delta = gross - fee if self.is_future(direction) else qty * price - fee
            p.qty -= qty
            if p.qty == ZERO:
                p.closed_index = len(self.trades)
                net = p.realized + p.funding - p.fees
                self.trades.append(
                    {
                        "asset": q.asset,
                        "direction": direction,
                        "entry_ms": p.entered_ms,
                        "exit_ms": timestamp,
                        "net": net,
                        "funding": p.funding,
                        "reason": reason,
                    }
                )
                if self.market == Market.STOCK:
                    self.year_gain += net
                    self.tax_reserve = (
                        max(ZERO, self.year_gain - self.cfg.tax_exempt_usd) * self.cfg.tax_pct
                    )
                self.positions.pop(q.asset)
                self.flat_ms[q.asset] = timestamp + self.scenario.ack_min * MINUTE
        self.cash += cash_delta
        self.fees += fee
        slip = qty * abs(price - raw)
        self.slips += slip
        self.turnover += qty * price
        self.busy_until_ms[q.asset] = timestamp + self.scenario.ack_min * MINUTE
        self.fills.append(
            {
                "at_ms": timestamp,
                "asset": q.asset,
                "action": action,
                "direction": direction,
                "quantity": qty,
                "price": price,
                "raw_price": raw,
                "fee": fee,
                "slip": slip,
                "cash_delta": cash_delta,
                "intent_id": intent,
                "reason": reason,
                "reference": reference
                if reference is not None
                else self.orders[q.asset].reference
                if q.asset in self.orders
                else ZERO,
            }
        )
        if self.cash - self.tax_reserve < -D("0.000000001"):
            raise DataIntegrityError("cash/tax overspent")

    def _orders(self, frame: Frame) -> None:
        for q in frame.quotes:
            order = self.orders.get(q.asset)
            if order is None or frame.at_ms < self.busy_until_ms.get(q.asset, -1):
                continue
            state = self.states.get(q.asset, State())
            if order.action == Action.OPEN and (
                state.wanted != order.direction
                or not self._guard(frame)
                or not self._fresh(q.asset, frame.at_ms)
            ):
                self.orders.pop(q.asset)
                self.counts["open_order_cancelled"] += 1
                continue
            if order.attempts >= self.cfg.max_attempts or frame.at_ms >= order.expires_ms:
                self.orders.pop(q.asset)
                if order.action == Action.OPEN:
                    self.expired_epoch[q.asset] = frame.at_ms // H4
                self.counts["order_expired"] += 1
                continue
            bar = q.bar(order.direction)
            if not bar.observed:
                continue
            order.attempts += 1
            buy = (order.action == Action.OPEN) != self.is_future(order.direction)
            _, slip = self.fee_slip(order.direction)
            price = bar.o * (ONE + (ONE if buy else -ONE) * slip * self.scenario.cost)
            if (buy and price > order.reference * (ONE + self.cfg.cap_pct)) or (
                not buy and price < order.reference * (ONE - self.cfg.cap_pct)
            ):
                self.counts["limit_miss"] += 1
                continue
            p = self.positions.get(q.asset)
            held = p.qty if p else ZERO
            if order.action == Action.OPEN:
                qty = max(
                    ZERO,
                    min(
                        order.wanted_qty - held, self._size(q, frame, order.direction, price) - held
                    ),
                )
            else:
                qty = max(ZERO, held - order.wanted_qty)
            qty = min(
                qty, q.bar(order.direction, previous=True).volume * self.scenario.participation
            )
            step = self.cfg.future_step if self.is_future(order.direction) else self.cfg.spot_step
            qty = floor_step(qty, step)
            if bar.volume <= ZERO or qty * price < self.cfg.min_notional:
                self.counts["liquidity_or_min_veto"] += 1
                continue
            self._fill(
                q,
                frame,
                order.action,
                order.direction,
                qty,
                bar.o,
                price,
                order.intent_id,
                order.reason,
            )
            remaining = self.positions[q.asset].qty if q.asset in self.positions else ZERO
            done = (
                remaining >= order.wanted_qty
                if order.action == Action.OPEN
                else remaining <= order.wanted_qty
            )
            if done:
                self.orders.pop(q.asset)
            else:
                self.counts["partial_fill"] += 1

    def _fund(
        self,
        frame: Frame,
        before: dict[str, Position | None],
        before_qty: dict[str, Decimal],
        *,
        exact: bool,
    ) -> None:
        for q, events in zip(frame.quotes, frame.funding, strict=False):
            for event in events:
                if (event.at_ms == frame.at_ms) != exact:
                    continue
                if not frame.at_ms <= event.at_ms < frame.end_ms:
                    raise CausalityError("funding outside frame")
                old = before.get(q.asset)
                current = self.positions.get(q.asset)
                old_qty = before_qty.get(q.asset, ZERO)
                new_qty = current.qty if current and self.is_future(current.direction) else ZERO
                qty = (
                    old_qty
                    if exact
                    else min(old_qty, new_qty)
                    if event.rate >= ZERO
                    else max(old_qty, new_qty)
                )
                owner = old if old_qty >= new_qty else current
                if not exact and owner and q.short.h >= owner.stop and event.rate > ZERO:
                    qty = ZERO
                    self.counts["funding_ambiguous_credit_omitted"] += 1
                amount = qty * event.mark * event.rate
                self.cash += amount
                self.funding_total += amount
                if owner and qty > ZERO:
                    owner.funding += amount
                    if owner.closed_index >= 0:
                        self.trades[owner.closed_index]["net"] += amount
                        self.trades[owner.closed_index]["funding"] += amount
                self.rates[q.asset] = event.rate
                self.rate_ms[q.asset] = event.at_ms
                if qty > ZERO:
                    self.funding_events.append(
                        {
                            "at_ms": event.at_ms,
                            "asset": q.asset,
                            "quantity": qty,
                            "rate": event.rate,
                            "mark": event.mark,
                            "amount": amount,
                            "conservative_boundary": not exact,
                        }
                    )

    def _protection(self, frame: Frame) -> None:
        for q in frame.quotes:
            p = self.positions.get(q.asset)
            if p is None or p.direction != Direction.SHORT:
                continue
            bar = q.short
            if not bar.observed:
                continue
            hit = bar.h >= p.stop if self.is_future(p.direction) else bar.low <= p.stop
            if not hit:
                continue
            p.stop_pending = True
            _, slip = self.fee_slip(p.direction)
            if self.is_future(p.direction):
                raw = max(bar.o, p.stop)
                price = raw * (ONE + (slip + self.cfg.future_stop_slip_pct) * self.scenario.cost)
                qty = p.qty
                reason = "EXCHANGE_STOP_MARKET_EXCEPTION"
            else:
                raw = min(bar.o, p.stop)
                price = raw * (ONE - slip * self.scenario.cost)
                if price < p.stop * (ONE - self.cfg.cap_pct):
                    self.counts["stop_limit_gap_unfilled"] += 1
                    continue
                qty = floor_step(
                    min(p.qty, q.previous_short.volume * self.scenario.participation),
                    self.cfg.spot_step,
                )
                if bar.volume <= ZERO or qty * price < self.cfg.min_notional:
                    continue
                reason = "ETF_STOP_LIMIT_PROXY"
            self._fill(
                q,
                frame,
                Action.CLOSE,
                p.direction,
                qty,
                raw,
                price,
                p.epoch + ":PROTECT",
                reason,
                at_ms=frame.end_ms,
                reference=p.stop,
            )
            self.orders.pop(q.asset, None)
            if q.asset not in self.positions:
                self.states[q.asset] = State(Direction.FLAT, p.epoch + ":RISK_EXIT", frame.end_ms)

    def tick(self, frame: Frame) -> None:
        for q in frame.quotes:
            for bar in (q.long, q.short, q.previous_long, q.previous_short):
                valid_bar(bar)
        old_trade_count = len(self.trades)
        before = {q.asset: self.positions.get(q.asset) for q in frame.quotes}
        quantities: dict[str, Decimal] = {}
        for q in frame.quotes:
            position = before[q.asset]
            quantities[q.asset] = (
                position.qty if position and self.is_future(position.direction) else ZERO
            )
        self._fund(frame, before, quantities, exact=True)
        self._plan_orders(frame)
        self._orders(frame)
        self._fund(frame, before, quantities, exact=False)
        self._protection(frame)
        for trade in self.trades[old_trade_count:]:
            self.streak = self.streak + 1 if trade["net"] < ZERO else 0
            if self.streak >= self.cfg.loss_streak:
                self.cooldown_until_ms = frame.end_ms + self.cfg.streak_wait_ms
                self.streak = 0
                self.counts["loss_streak_cooldown"] += 1

    def checkpoint(self, frame: Frame) -> None:
        equity = self.equity(frame.quotes, "c")
        self.peak = max(self.peak, equity)
        self.last_equity = equity
        if equity <= self.peak * (ONE - self.cfg.drawdown_pct):
            if not self.halted:
                self.halted_at_ms = frame.end_ms
            self.halted = True
        self.trace.append(
            {
                "at_ms": frame.end_ms,
                "day": frame.day,
                "equity": equity,
                "cash": self.cash,
                "tax_reserve": self.tax_reserve,
                "held": {
                    k: {"direction": v.direction, "quantity": v.qty}
                    for k, v in self.positions.items()
                },
                "halted": self.halted,
                "long_exposure_pct": sum(
                    (
                        p.qty * q.long.c
                        for q in frame.quotes
                        if (p := self.positions.get(q.asset)) and p.direction == Direction.LONG
                    ),
                    ZERO,
                )
                / equity
                if equity > ZERO
                else ZERO,
                "short_exposure_pct": sum(
                    (
                        p.qty * q.short.c
                        for q in frame.quotes
                        if (p := self.positions.get(q.asset)) and p.direction == Direction.SHORT
                    ),
                    ZERO,
                )
                / equity
                if equity > ZERO
                else ZERO,
            }
        )

    def active(self, at_ms: int) -> bool:
        return (
            bool(self.orders)
            or any(p.direction == Direction.SHORT for p in self.positions.values())
            or any(
                p.direction != self.states.get(asset, State()).wanted
                for asset, p in self.positions.items()
            )
            or any(
                not self.halted
                and s.wanted != Direction.FLAT
                and asset not in self.positions
                and self._fresh(asset, at_ms)
                and self.expired_epoch.get(asset) != at_ms // H4
                for asset, s in self.states.items()
            )
        )

    def prepare(self, frame: Frame) -> None:
        """Queue resizing at the decision boundary before selecting minute consumers."""
        self._plan_orders(frame)

    def result(self, quotes: tuple[Quote, ...]) -> dict[str, Any]:
        reserve = sum(
            (
                p.qty * q.bar(p.direction).c * sum(self.fee_slip(p.direction)) * self.scenario.cost
                for q in quotes
                if (p := self.positions.get(q.asset)) is not None
            ),
            ZERO,
        )
        net = self.equity(quotes, "c") - self.cfg.initial_cash - reserve
        return {
            "case": self.key,
            "net": net,
            "terminal_equity": self.equity(quotes, "c"),
            "terminal_reserve": reserve,
            "cash": self.cash,
            "tax_paid": self.tax_paid,
            "tax_reserve": self.tax_reserve,
            "fees": self.fees,
            "slips": self.slips,
            "funding": self.funding_total,
            "turnover": self.turnover,
            "halted_at_ms": self.halted_at_ms,
            "positions": {
                k: {"direction": v.direction, "quantity": v.qty, "entry": v.entry}
                for k, v in self.positions.items()
            },
            "fills": self.fills,
            "funding_events": self.funding_events,
            "trades": self.trades,
            "evaluations": self.evaluations,
            "trace": self.trace,
            "pending_orders": len(self.orders),
            "counts": dict(self.counts),
            "operating_parity": "UNVERIFIED",
        }
