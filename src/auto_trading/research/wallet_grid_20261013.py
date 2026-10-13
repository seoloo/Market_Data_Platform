"""Registered explanatory wallet/grid accounting, never an operating strategy."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Any, Literal, Protocol

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.core.money import floor_to_step

D = Decimal
ZERO, ONE = D(0), D(1)
MINUTE, H4, DAY = 60_000, 14_400_000, 86_400_000
INITIAL, CAP, BAND = D(10000), D("0.003"), D("0.05")
SPOT_STEP, FUT_STEP, MIN_NOTIONAL = D("0.000001"), D("0.001"), D(5)
PARTICIPATION = D("0.001")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
Mode = Literal["BASE_WALLET", "PRIORITY_4H", "PRIORITY_1M"]


@dataclass(frozen=True)
class Bar:
    o: Decimal
    h: Decimal
    low: Decimal
    c: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Minute:
    at_ms: int
    spot: tuple[Bar, Bar]
    perp: tuple[Bar, Bar]
    previous_spot: tuple[Bar, Bar]
    previous_perp: tuple[Bar, Bar]
    previous_as_of_ms: int


class MinuteProvider(Protocol):
    def get(self, index: int) -> Minute: ...


@dataclass(frozen=True)
class Funding:
    at_ms: int
    rate: Decimal
    mark: Decimal
    exact_mark: bool


@dataclass(frozen=True)
class Block:
    at_ms: int
    spot: tuple[Bar, Bar]
    perp: tuple[Bar, Bar]
    previous_spot: tuple[Bar, Bar]
    previous_perp: tuple[Bar, Bar]
    targets: tuple[Decimal, Decimal] | None
    target_as_of_ms: int
    signals: tuple[bool, bool]
    signal_as_of_ms: int
    funding: tuple[tuple[Funding, ...], tuple[Funding, ...]]
    minutes: MinuteProvider


@dataclass(frozen=True)
class Scenario:
    name: str
    cost: int = 1
    participation: Decimal = PARTICIPATION
    ack_min: int = 1


SCENARIOS = (
    Scenario("BASE"),
    Scenario("COST2", cost=2),
    Scenario("THIN", participation=D("0.00025")),
    Scenario("ACK5", ack_min=5),
)
RATIOS = (D("0.85"), D("0.90"), D("0.70"))


@dataclass(frozen=True)
class Case:
    spot_ratio: Decimal
    mode: Mode
    scenario: Scenario

    @property
    def key(self) -> str:
        return f"{self.spot_ratio}:{self.mode}:{self.scenario.name}"

    @property
    def return_grid_ms(self) -> int:
        return MINUTE if self.mode == "PRIORITY_1M" else H4


def cases() -> tuple[Case, ...]:
    modes: tuple[Mode, ...] = ("BASE_WALLET", "PRIORITY_4H", "PRIORITY_1M")
    return tuple(
        Case(ratio, mode, scenario) for scenario in SCENARIOS for ratio in RATIOS for mode in modes
    )


def valid_bar(bar: Bar) -> None:
    if not all(
        isinstance(v, Decimal) and v.is_finite() for v in (bar.o, bar.h, bar.low, bar.c, bar.volume)
    ):
        raise DataIntegrityError("finite Decimal OHLCV required")
    if not ZERO < bar.low <= min(bar.o, bar.c) <= max(bar.o, bar.c) <= bar.h or bar.volume < ZERO:
        raise DataIntegrityError("invalid execution OHLCV")


@dataclass
class Short:
    qty: Decimal
    entry: Decimal
    raw_entry: Decimal
    entry_ms: int
    opened_qty: Decimal
    gross: Decimal = ZERO
    fees: Decimal = ZERO
    slips: Decimal = ZERO
    funding: Decimal = ZERO


@dataclass
class Order:
    kind: Literal["BUY", "SELL", "OPEN", "CLOSE"]
    reference: Decimal
    remaining: Decimal
    created_ms: int
    expires_ms: int
    is_return: bool = False
    attempts: int = 0
    next_ms: int = 0
    goal_version_ms: int = 0


@dataclass
class Return:
    requested_ms: int
    reference: Decimal
    goal_version_ms: int
    first_ms: int | None = None
    full_ms: int | None = None
    quantity_at_buy: Decimal | None = None
    bought: Decimal = ZERO
    buy_attempts: int = 0
    window_expired: bool = False
    versions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Account:
    case: Case
    spot_cash: Decimal = ZERO
    futures_cash: Decimal = ZERO
    spot_qty: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    goals: list[Decimal] = field(default_factory=lambda: [ZERO, ZERO])
    shorts: dict[int, Short] = field(default_factory=dict)
    orders: dict[tuple[int, str], Order] = field(default_factory=dict)
    returns: dict[int, Return] = field(default_factory=dict)
    last_closed_ack: list[int] = field(default_factory=lambda: [-DAY, -DAY])
    busy_until_ms: int = -1
    cooldown_until_ms: int = -1
    peak: Decimal = INITIAL
    day_start: Decimal = INITIAL
    halted: bool = False
    streak: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    spot_raw_flow: Decimal = ZERO
    futures_gross: Decimal = ZERO
    spot_fee: Decimal = ZERO
    futures_fee: Decimal = ZERO
    spot_slip: Decimal = ZERO
    futures_slip: Decimal = ZERO
    funding: Decimal = ZERO
    turnover: Decimal = ZERO
    mdd: Decimal = ZERO
    dd_bound: Decimal = ZERO
    exposure_sum: Decimal = ZERO
    exposure_max: Decimal = ZERO
    trace: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    return_events: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    last_rates: list[Decimal | None] = field(default_factory=lambda: [None, None])

    def __post_init__(self) -> None:
        self.spot_cash = INITIAL * self.case.spot_ratio
        self.futures_cash = INITIAL - self.spot_cash

    def equity(
        self, spot: tuple[Bar, Bar], perp: tuple[Bar, Bar], field_name: Literal["o", "c"]
    ) -> Decimal:
        return (
            self.spot_cash
            + self.futures_cash
            + sum(
                (
                    self.spot_qty[i] * (spot[i].o if field_name == "o" else spot[i].c)
                    for i in range(2)
                ),
                ZERO,
            )
            + sum(
                (
                    p.qty * (p.entry - (perp[i].o if field_name == "o" else perp[i].c))
                    for i, p in self.shorts.items()
                ),
                ZERO,
            )
        )


def _allowed(a: Account, spot: tuple[Bar, Bar], perp: tuple[Bar, Bar], at_ms: int) -> bool:
    e = a.equity(spot, perp, "o")
    if e <= ZERO or e <= a.peak * D("0.75"):
        if not a.halted:
            a.counts["halt_dd25"] += 1
        a.halted = True
    if a.halted or e <= a.day_start * D("0.98") or at_ms < a.cooldown_until_ms:
        a.counts["new_exposure_risk_veto"] += 1
        return False
    return True


def _record_return(a: Account, i: int, reason: str) -> None:
    r = a.returns.pop(i)
    a.return_events.append(
        {
            "symbol": SYMBOLS[i],
            "requested_ms": r.requested_ms,
            "first_ms": r.first_ms,
            "full_ms": r.full_ms,
            "quantity_at_buy": r.quantity_at_buy,
            "bought": r.bought,
            "last_goal_attempts": r.buy_attempts,
            "versions": r.versions,
            "status": reason,
        }
    )


def _finish_short(a: Account, i: int, at_ms: int, reason: str, emergency: bool) -> None:
    p = a.shorts.pop(i)
    net = p.gross - p.fees - p.slips + p.funding
    a.trades.append(
        {
            "symbol": SYMBOLS[i],
            "entry_ms": p.entry_ms,
            "exit_ms": at_ms,
            "qty": p.opened_qty,
            "market_gross": p.gross,
            "fee": p.fees,
            "slip": p.slips,
            "funding": p.funding,
            "net": net,
            "reason": reason,
        }
    )
    a.counts["short_closed"] += 1
    ack = at_ms + a.case.scenario.ack_min * MINUTE + (MINUTE if emergency else 0)
    a.busy_until_ms = max(a.busy_until_ms, ack)
    a.last_closed_ack[i] = ack
    a.cooldown_until_ms = max(a.cooldown_until_ms, ack + 60 * MINUTE)
    a.orders.pop((i, "CLOSE"), None)


def _close(a: Account, i: int, m: Minute, *, emergency: bool = False) -> bool:
    p, scenario = a.shorts[i], a.case.scenario
    bar, previous = m.perp[i], m.previous_perp[i]
    order = a.orders.get((i, "CLOSE"))
    if not emergency and order is None:
        return False
    raw = max(bar.o, p.entry * D("1.03")) if emergency else bar.o
    price = raw * (
        ONE + D("0.00045") * scenario.cost + (D("0.002") * scenario.cost if emergency else ZERO)
    )
    if not emergency:
        assert order is not None
        if m.at_ms < order.next_ms or m.at_ms < a.busy_until_ms:
            return False
        grid = a.case.return_grid_ms if order.is_return else MINUTE
        if order.is_return and m.at_ms % grid:
            return False
        order.next_ms = m.at_ms + max(grid, scenario.ack_min * MINUTE)
        order.attempts += 1
        if price > order.reference * (ONE + CAP):
            a.counts["short_close_cap_miss"] += 1
            return False
        qty = min(p.qty, floor_to_step(previous.volume * scenario.participation, FUT_STEP))
        if bar.volume == ZERO:
            qty = ZERO
    else:
        qty = p.qty
    if qty <= ZERO:
        a.counts["short_close_no_liquidity"] += 1
        return False
    fee = qty * price * D("0.0005") * scenario.cost
    slip, gross = qty * (price - raw), qty * (p.raw_entry - raw)
    a.futures_cash += qty * (p.entry - price) - fee
    a.futures_gross += gross
    a.futures_fee += fee
    a.futures_slip += slip
    a.turnover += qty * price
    p.gross += gross
    p.fees += fee
    p.slips += slip
    a.counts["short_close_partial"] += int(qty < p.qty)
    p.qty -= qty
    reason = (
        "STOP" if emergency else "LONG_PRIORITY" if order is not None and order.is_return else "48H"
    )
    a.counts[f"close_piece:{reason}"] += 1
    a.fills.append(
        {
            "at_ms": m.at_ms,
            "symbol": SYMBOLS[i],
            "leg": reason,
            "qty": qty,
            "price": price,
            "fee": fee,
        }
    )
    a.busy_until_ms = max(a.busy_until_ms, m.at_ms + scenario.ack_min * MINUTE)
    if p.qty == ZERO:
        _finish_short(a, i, m.at_ms, reason, emergency)
    return True


def _open(a: Account, i: int, m: Minute, end_ms: int) -> None:
    order, scenario = a.orders[i, "OPEN"], a.case.scenario
    if m.at_ms >= order.expires_ms or order.attempts >= 3:
        a.orders.pop((i, "OPEN"))
        a.counts["short_open_expired"] += 1
        return
    if m.at_ms < a.busy_until_ms or m.at_ms < order.next_ms:
        return
    if (
        a.goals[i] > ZERO
        or a.spot_qty[i] > ZERO
        or i in a.shorts
        or m.at_ms >= end_ms - 144 * 60 * MINUTE
    ):
        a.orders.pop((i, "OPEN"))
        a.counts["short_long_or_overlap_veto"] += 1
        return
    if not _allowed(a, m.spot, m.perp, m.at_ms):
        return
    rate = a.last_rates[i]
    if rate is None:
        a.counts["funding_unknown_veto"] += 1
        return
    order.attempts += 1
    order.next_ms = m.at_ms + scenario.ack_min * MINUTE
    bar, previous = m.perp[i], m.previous_perp[i]
    price = bar.o * (ONE - D("0.00045") * scenario.cost)
    if price < order.reference * (ONE - CAP):
        a.counts["short_open_cap_miss"] += 1
        return
    e = a.equity(m.spot, m.perp, "o")
    fee_pct = D("0.0005") * scenario.cost
    margin_used = sum((p.qty * p.entry for p in a.shorts.values()), ZERO)
    unrealized = sum((p.qty * (p.entry - m.perp[j].o) for j, p in a.shorts.items()), ZERO)
    free = max(ZERO, a.futures_cash + min(ZERO, unrealized) - D("1.5") * margin_used)
    gross_now = sum((a.spot_qty[j] * m.spot[j].o for j in range(2)), ZERO) + sum(
        (p.qty * m.perp[j].o for j, p in a.shorts.items()), ZERO
    )
    cost_pct = D("0.0039") * scenario.cost + max(ZERO, -rate) * 6
    stop_multiplier = D("1.03") * (ONE + D("0.00245") * scenario.cost)
    exact_risk = (
        (bar.o / price - ONE)
        + stop_multiplier
        - ONE
        + fee_pct * (ONE + stop_multiplier)
        + max(ZERO, -rate) * 6
    )
    entry_loss_pct = bar.o / price - ONE + fee_pct
    notional = min(
        e * D("0.25"),
        e * D("0.10") / (ONE + D("0.10") * entry_loss_pct),
        e * D("0.01") / max(D("0.03") + cost_pct, exact_risk),
        free / (D("1.5") + fee_pct),
        max(ZERO, e - gross_now) / (2 * bar.o / price - ONE + fee_pct),
    )
    qty = floor_to_step(min(notional / price, previous.volume * scenario.participation), FUT_STEP)
    if bar.volume == ZERO:
        qty = ZERO
    if qty <= ZERO or qty * price < MIN_NOTIONAL:
        a.counts["short_open_cash_liquidity_min_veto"] += 1
        return
    fee, slip = qty * price * fee_pct, qty * (bar.o - price)
    a.futures_cash -= fee
    a.futures_fee += fee
    a.futures_slip += slip
    a.turnover += qty * price
    a.shorts[i] = Short(qty, price, bar.o, m.at_ms, qty, fees=fee, slips=slip)
    a.orders.pop((i, "OPEN"))
    a.counts["short_opened"] += 1
    a.counts["short_entry_liquidity_reduced"] += int(qty * price < notional)
    a.busy_until_ms = m.at_ms + scenario.ack_min * MINUTE
    a.fills.append(
        {
            "at_ms": m.at_ms,
            "symbol": SYMBOLS[i],
            "leg": "OPEN_SHORT",
            "qty": qty,
            "price": price,
            "fee": fee,
            "equity_before": e,
            "margin_pct": qty * price / e,
        }
    )


def _spot(a: Account, i: int, m: Minute) -> None:
    order, scenario = a.orders[i, "SPOT"], a.case.scenario
    buying = order.kind == "BUY"
    if buying and (m.at_ms >= order.expires_ms or order.attempts >= 3 or a.goals[i] == ZERO):
        a.orders.pop((i, "SPOT"))
        a.counts["spot_buy_expired_or_canceled"] += 1
        if order.is_return and order.attempts >= 3:
            a.counts["return_attempt_exhausted"] += 1
        return
    if m.at_ms < a.busy_until_ms or m.at_ms < order.next_ms:
        return
    grid = a.case.return_grid_ms if order.is_return else MINUTE
    if order.is_return and m.at_ms % grid:
        return
    if buying and (
        i in a.shorts
        or m.at_ms < a.last_closed_ack[i] + 60 * MINUTE
        or not _allowed(a, m.spot, m.perp, m.at_ms)
    ):
        a.counts["spot_buy_risk_or_short_veto"] += 1
        return
    order.next_ms = m.at_ms + max(grid, scenario.ack_min * MINUTE)
    order.attempts += 1
    if order.is_return:
        a.returns[i].buy_attempts = order.attempts
        a.counts["return_buy_attempts"] += 1
    bar, previous = m.spot[i], m.previous_spot[i]
    price = bar.o * (
        ONE + D("0.0005") * scenario.cost if buying else ONE - D("0.0005") * scenario.cost
    )
    if (buying and price > order.reference * (ONE + CAP)) or (
        not buying and price < order.reference * (ONE - CAP)
    ):
        a.counts["spot_buy_cap_miss" if buying else "spot_sell_cap_miss"] += 1
        return
    qty = min(order.remaining, floor_to_step(previous.volume * scenario.participation, SPOT_STEP))
    fee_pct = D("0.001") * scenario.cost
    if buying:
        qty = min(qty, floor_to_step(max(ZERO, a.spot_cash) / (price * (ONE + fee_pct)), SPOT_STEP))
        e = a.equity(m.spot, m.perp, "o")
        gross = sum((a.spot_qty[j] * m.spot[j].o for j in range(2)), ZERO) + sum(
            (p.qty * m.perp[j].o for j, p in a.shorts.items()), ZERO
        )
        total_room = max(ZERO, e - gross) / (ONE + fee_pct)
        coin_room_qty = max(ZERO, D("0.5") * e - a.spot_qty[i] * bar.o) / (
            bar.o + D("0.5") * (price - bar.o + price * fee_pct)
        )
        cap_qty = floor_to_step(min(total_room / price, coin_room_qty), SPOT_STEP)
        if cap_qty < qty:
            a.counts["spot_exposure_cap_reduced"] += 1
        qty = min(qty, cap_qty)
    else:
        qty = min(qty, a.spot_qty[i])
    if bar.volume == ZERO:
        qty = ZERO
    if qty <= ZERO or (buying and qty * price < MIN_NOTIONAL):
        a.counts["spot_cash_liquidity_min_veto"] += 1
        return
    fee, slip = qty * price * fee_pct, qty * abs(price - bar.o)
    a.spot_cash += -qty * price - fee if buying else qty * price - fee
    a.spot_qty[i] += qty if buying else -qty
    a.spot_raw_flow += -qty * bar.o if buying else qty * bar.o
    a.spot_fee += fee
    a.spot_slip += slip
    a.turnover += qty * price
    a.counts["spot_fills"] += 1
    a.counts["spot_partial"] += int(qty < order.remaining)
    order.remaining -= qty
    a.busy_until_ms = m.at_ms + scenario.ack_min * MINUTE
    if not buying and a.spot_qty[i] == ZERO:
        a.cooldown_until_ms = max(a.cooldown_until_ms, a.busy_until_ms + 60 * MINUTE)
    a.fills.append(
        {
            "at_ms": m.at_ms,
            "symbol": SYMBOLS[i],
            "leg": "BUY_SPOT" if buying else "SELL_SPOT",
            "qty": qty,
            "price": price,
            "fee": fee,
            "is_return": order.is_return,
        }
    )
    if buying and i in a.returns:
        r = a.returns[i]
        r.bought += qty
        if r.first_ms is None:
            r.first_ms = m.at_ms
            a.counts["return_first_buy"] += 1
    if order.remaining == ZERO:
        a.orders.pop((i, "SPOT"))
        if buying and i in a.returns:
            a.returns[i].full_ms = m.at_ms
            a.counts["return_buy_quantity_completed"] += 1
            _record_return(a, i, "BUY_QUANTITY_REACHED")


def _begin(a: Account, b: Block) -> None:
    at = b.at_ms
    e = a.equity(b.spot, b.perp, "o")
    a.peak = max(a.peak, e)
    if at % DAY == 0:
        a.day_start = e
    _allowed(a, b.spot, b.perp, at)
    if b.targets is not None:
        if (
            b.target_as_of_ms > at
            or any(not w.is_finite() or not ZERO <= w <= D("0.5") for w in b.targets)
            or sum(b.targets) > ONE
        ):
            raise CausalityError("invalid/future T/v2 target")
        a.goals = list(b.targets)
        for i, r in list(a.returns.items()):
            if a.goals[i] == ZERO:
                _record_return(a, i, "GOAL_ZERO")
                a.counts["return_goal_zero_before_buy"] += int(r.first_ms is None)
                a.orders.pop((i, "SPOT"), None)
            elif r.goal_version_ms != at:
                r.versions.append(
                    {
                        "goal_version_ms": r.goal_version_ms,
                        "attempts": r.buy_attempts,
                        "window_expired": r.window_expired,
                    }
                )
                r.goal_version_ms, r.reference = at, b.previous_spot[i].c
                r.buy_attempts, r.window_expired = 0, False
                a.orders.pop((i, "SPOT"), None)
                a.counts["return_goal_revision"] += 1
    for i in range(2):
        if i in a.shorts and a.goals[i] > ZERO:
            if i not in a.returns:
                a.returns[i] = Return(at, b.previous_spot[i].c, at // DAY * DAY)
                a.counts["return_requests"] += 1
            old = a.orders.get((i, "CLOSE"))
            a.orders[i, "CLOSE"] = Order(
                "CLOSE",
                b.previous_perp[i].c,
                a.shorts[i].qty,
                at,
                at + H4,
                True,
                next_ms=at if old is None else max(at, old.next_ms),
            )
        elif (i, "CLOSE") in a.orders:
            a.orders[i, "CLOSE"].reference = b.previous_perp[i].c
        if i in a.returns:
            continue
        wanted = floor_to_step(a.goals[i] * max(ZERO, e) / b.spot[i].o, SPOT_STEP)
        change = wanted - a.spot_qty[i]
        drift = abs(a.goals[i] - a.spot_qty[i] * b.spot[i].o / e) if e > ZERO else ONE
        if change < ZERO and (a.goals[i] == ZERO or drift > BAND):
            a.orders[i, "SPOT"] = Order("SELL", b.previous_spot[i].c, -change, at, at + H4)
        elif change > ZERO and drift > BAND and i not in a.shorts:
            a.orders[i, "SPOT"] = Order("BUY", b.previous_spot[i].c, change, at, at + H4)
        else:
            a.orders.pop((i, "SPOT"), None)
        if b.signals[i] and a.case.mode != "BASE_WALLET":
            if b.signal_as_of_ms > at:
                raise CausalityError("future RSI signal")
            if a.goals[i] > ZERO or a.spot_qty[i] > ZERO or i in a.shorts:
                a.counts["short_signal_conflict"] += 1
            else:
                a.orders[i, "OPEN"] = Order("OPEN", b.previous_perp[i].c, ZERO, at, at + H4)


def _fund(a: Account, i: int, event: Funding, quantity: Decimal, stopped: bool) -> None:
    amount = quantity * event.mark * event.rate
    if stopped and event.at_ms % MINUTE:
        amount = min(ZERO, amount)
    a.futures_cash += amount
    a.funding += amount
    a.last_rates[i] = event.rate
    if quantity > ZERO and not event.exact_mark:
        a.counts["funding_proxy_applied"] += 1
    if i in a.shorts:
        a.shorts[i].funding += amount
    elif amount:
        # A conservative sub-minute debit after a stop belongs to that closed trade.
        for trade in reversed(a.trades):
            if trade["symbol"] == SYMBOLS[i]:
                trade["funding"] += amount
                trade["net"] += amount
                break


def _stress(a: Account, spot: tuple[Bar, Bar], perp: tuple[Bar, Bar]) -> None:
    """Descriptive adverse OHLC combination, never used for trading decisions."""
    adverse = (
        a.spot_cash
        + a.futures_cash
        + sum((a.spot_qty[i] * spot[i].low for i in range(2)), ZERO)
        + sum((p.qty * (p.entry - perp[i].h) for i, p in a.shorts.items()), ZERO)
    )
    a.dd_bound = max(a.dd_bound, ONE - adverse / a.peak if a.peak > ZERO else ONE)


def _minute(
    a: Account, m: Minute, events: tuple[tuple[Funding, ...], tuple[Funding, ...]], end_ms: int
) -> None:
    first_trade = len(a.trades)
    _stress(a, m.spot, m.perp)
    before_stop = {i: p.qty for i, p in a.shorts.items()}
    stopped: set[int] = set()
    for i in range(2):
        for event in events[i]:
            if event.at_ms == m.at_ms:
                _fund(a, i, event, a.shorts[i].qty if i in a.shorts else ZERO, False)
    for i in list(a.shorts):
        p = a.shorts[i]
        if m.perp[i].h >= p.entry * D("1.03"):
            _close(a, i, m, emergency=True)
            stopped.add(i)
        elif m.at_ms >= p.entry_ms + 48 * 60 * MINUTE:
            old = a.orders.get((i, "CLOSE"))
            if old is None or old.is_return:
                a.orders[i, "CLOSE"] = Order("CLOSE", m.previous_perp[i].c, p.qty, m.at_ms, end_ms)
    for i in range(2):
        if i in a.shorts and (i, "CLOSE") in a.orders:
            _close(a, i, m)
        if i in a.returns and i not in a.shorts and a.goals[i] > ZERO:
            r = a.returns[i]
            eligible = a.last_closed_ack[i] + 60 * MINUTE
            # For a new daily goal version after an expired window, recheck anew.
            eligible = max(eligible, r.goal_version_ms)
            until = min(eligible + 240 * MINUTE, r.goal_version_ms + DAY)
            if m.at_ms >= until and not r.window_expired:
                r.window_expired = True
                a.counts["return_window_expired"] += 1
            if eligible <= m.at_ms < until and r.buy_attempts < 3 and (i, "SPOT") not in a.orders:
                desired = floor_to_step(
                    a.goals[i] * max(ZERO, a.equity(m.spot, m.perp, "o")) / m.spot[i].o, SPOT_STEP
                )
                remaining = max(ZERO, desired - a.spot_qty[i])
                if remaining > ZERO:
                    a.orders[i, "SPOT"] = Order(
                        "BUY",
                        r.reference,
                        remaining,
                        m.at_ms,
                        until,
                        True,
                        attempts=r.buy_attempts,
                        goal_version_ms=r.goal_version_ms,
                    )
                    r.quantity_at_buy = remaining
                elif a.spot_qty[i] > ZERO:
                    r.full_ms = m.at_ms
                    _record_return(a, i, "LATEST_GOAL_ALREADY_REACHED")
        if (i, "SPOT") in a.orders:
            _spot(a, i, m)
        if (i, "OPEN") in a.orders:
            _open(a, i, m, end_ms)
        _stress(a, m.spot, m.perp)
        if i in a.shorts and m.perp[i].h >= a.shorts[i].entry * D("1.03"):
            before_stop[i] = a.shorts[i].qty
            _close(a, i, m, emergency=True)
            stopped.add(i)
    for i in range(2):
        for event in events[i]:
            if event.at_ms > m.at_ms:
                quantity = (
                    before_stop.get(i, ZERO)
                    if i in stopped
                    else a.shorts[i].qty
                    if i in a.shorts
                    else ZERO
                )
                _fund(a, i, event, quantity, i in stopped)
    for trade in a.trades[first_trade:]:
        a.streak = a.streak + 1 if trade["net"] < ZERO else 0
        if a.streak >= 4:
            a.cooldown_until_ms = max(a.cooldown_until_ms, a.busy_until_ms + 240 * MINUTE)
            a.streak = 0
            a.counts["four_loss_cooldown"] += 1
    _stress(a, m.spot, m.perp)
    if a.spot_cash < ZERO:
        raise DataIntegrityError("spot cash overspent")


def _checkpoint(a: Account, b: Block) -> None:
    e = a.equity(b.spot, b.perp, "c")
    a.peak = max(a.peak, e)
    if e <= ZERO or e <= a.peak * D("0.75"):
        if not a.halted:
            a.counts["halt_dd25"] += 1
        a.halted = True
    dd = ONE - e / a.peak if a.peak > ZERO else ONE
    a.mdd = max(a.mdd, dd)
    gross = sum((a.spot_qty[i] * b.spot[i].c for i in range(2)), ZERO) + sum(
        (p.qty * b.perp[i].c for i, p in a.shorts.items()), ZERO
    )
    exposure = gross / e if e > ZERO else ZERO
    a.exposure_sum += exposure
    a.exposure_max = max(a.exposure_max, exposure)
    margin = sum((p.qty * p.entry for p in a.shorts.values()), ZERO)
    a.counts["margin_buffer_warning"] += int(a.futures_cash < D("1.5") * margin)
    a.trace.append(
        {
            "at_ms": b.at_ms + H4,
            "equity": e,
            "spot_cash": a.spot_cash,
            "futures_cash": a.futures_cash,
            "spot_qty": tuple(a.spot_qty),
            "futures_qty": tuple(-a.shorts[i].qty if i in a.shorts else ZERO for i in range(2)),
            "exposure": exposure,
            "halted": a.halted,
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
        raise DataIntegrityError("unregistered cases")
    accounts = [Account(c) for c in chosen]
    previous_ms: int | None = None
    last: Block | None = None
    for block in blocks:
        if block.at_ms + H4 > end_ms or (
            previous_ms is not None and block.at_ms != previous_ms + H4
        ):
            raise DataIntegrityError("block gap/cutoff")
        for bar in (*block.spot, *block.perp, *block.previous_spot, *block.previous_perp):
            valid_bar(bar)
        if block.signal_as_of_ms > block.at_ms:
            raise CausalityError("future RSI signal")
        for group in block.funding:
            if any(x.at_ms >= y.at_ms for x, y in pairwise(group)):
                raise DataIntegrityError("funding duplicate/order")
            for event in group:
                if (
                    not block.at_ms <= event.at_ms < block.at_ms + H4
                    or not event.rate.is_finite()
                    or not event.mark.is_finite()
                    or event.mark <= ZERO
                ):
                    raise DataIntegrityError("funding schema/boundary")
        previous_ms, last = block.at_ms, block
        for a in accounts:
            _begin(a, block)
            if not a.orders and not a.shorts and not a.returns:
                _stress(a, block.spot, block.perp)
        by_minute: dict[int, list[list[Funding]]] = {}
        for i, group in enumerate(block.funding):
            for event in group:
                at = event.at_ms // MINUTE * MINUTE
                by_minute.setdefault(at, [[], []])[i].append(event)
        for n in range(H4 // MINUTE):
            at = block.at_ms + n * MINUTE
            active = [n for n, a in enumerate(accounts) if a.shorts or a.orders or a.returns]
            groups = by_minute.get(at, [[], []])
            events = (tuple(groups[0]), tuple(groups[1]))
            if active:
                minute = block.minutes.get(n)
                if minute.at_ms != at:
                    raise DataIntegrityError("minute gap/order")
                if minute.previous_as_of_ms != at:
                    raise CausalityError("previous minute must close exactly at current open")
                for bar in (
                    *minute.spot,
                    *minute.perp,
                    *minute.previous_spot,
                    *minute.previous_perp,
                ):
                    valid_bar(bar)
                for n in active:
                    _minute(accounts[n], minute, events, end_ms)
            for n, a in enumerate(accounts):
                if n not in active:
                    for i in range(2):
                        for event in events[i]:
                            a.last_rates[i] = event.rate
        for a in accounts:
            _checkpoint(a, block)
    if last is None or last.at_ms + H4 != end_ms:
        raise DataIntegrityError("empty/incomplete replay")
    result: dict[str, dict[str, Any]] = {}
    for a in accounts:
        for i in list(a.returns):
            _record_return(a, i, "END_UNRESOLVED")
        scenario = a.case.scenario
        spot_value = sum((a.spot_qty[i] * last.spot[i].c for i in range(2)), ZERO)
        spot_gross = a.spot_raw_flow + spot_value
        fut_gross = a.futures_gross + sum(
            (p.qty * (p.raw_entry - last.perp[i].c) for i, p in a.shorts.items()), ZERO
        )
        reserve = spot_value * D("0.0015") * scenario.cost + sum(
            (p.qty * last.perp[i].c * D("0.00095") * scenario.cost for i, p in a.shorts.items()),
            ZERO,
        )
        net = a.trace[-1]["equity"] - INITIAL - reserve
        residual = net - (
            spot_gross
            + fut_gross
            + a.funding
            - a.spot_fee
            - a.futures_fee
            - a.spot_slip
            - a.futures_slip
            - reserve
        )
        if abs(residual) > D("0.0000000001"):
            raise DataIntegrityError("wallet accounting residual")
        result[a.case.key] = {
            "case": {
                "spot_ratio": a.case.spot_ratio,
                "mode": a.case.mode,
                "scenario": scenario.name,
            },
            "net": net,
            "terminal_equity": a.trace[-1]["equity"],
            "terminal_cost_reserve": reserve,
            "spot_gross": spot_gross,
            "futures_gross": fut_gross,
            "spot_fee": a.spot_fee,
            "futures_fee": a.futures_fee,
            "spot_slip": a.spot_slip,
            "futures_slip": a.futures_slip,
            "funding": a.funding,
            "turnover": a.turnover,
            "mdd": a.mdd,
            "dd_bound": a.dd_bound,
            "mean_exposure": a.exposure_sum / len(a.trace),
            "max_exposure": a.exposure_max,
            "residual": residual,
            "terminal_spot_qty": tuple(a.spot_qty),
            "terminal_futures_qty": tuple(
                -a.shorts[i].qty if i in a.shorts else ZERO for i in range(2)
            ),
            "terminal_spot_cash": a.spot_cash,
            "terminal_futures_cash": a.futures_cash,
            "terminal_pending_orders": len(a.orders),
            "counts": dict(a.counts),
            "trace": a.trace,
            "trades": a.trades,
            "fills": a.fills,
            "returns": a.return_events,
            "operating_risk_parity": "NOT_VERIFIED",
        }
    return result
