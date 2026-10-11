"""209 frozen historical accounting pilot. Pure, isolated; never an order adapter.

Minute fills/mark prices are registered proxies, not historical exchange fills.
No I/O, wall clock, operational registry, random selection or parameter search.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.money import floor_to_step

D = Decimal
ZERO = D(0)
ONE = D(1)
DAY_MS = 86_400_000
MINUTE_MS = 60_000
START_MS = 1_583_020_800_000
END_MS = 1_788_220_800_000
SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class PilotConfig:
    name: str = "main"
    participation: Decimal = Decimal("0.01")
    mark_factor: Decimal = ONE
    fee_rate: Decimal = Decimal("0.00045")
    slippage_rate: Decimal = Decimal("0.0005")
    emergency_rate: Decimal = Decimal("0.01")
    capital: Decimal = Decimal("10000")
    initial_risk_pct: Decimal = Decimal("0.0025")
    risk_cap_pct: Decimal = Decimal("0.01")
    margin_cap_pct: Decimal = Decimal("0.1")
    leverage: Decimal = Decimal(3)
    gross_cap: Decimal = Decimal("1.5")
    funding_reserve: Decimal = Decimal("0.001")
    maintenance_rate: Decimal = Decimal("0.004")
    daily_loss_pct: Decimal = Decimal("0.02")
    halt_dd_pct: Decimal = Decimal("0.30")
    cap_pct: Decimal = Decimal("0.003")
    max_rounding_pct: Decimal = Decimal("0.05")
    loss_limit: int = 4
    cooldown_ms: int = 240 * MINUTE_MS
    reentry_ms: int = 60 * MINUTE_MS
    max_attempts: int = 3

    @property
    def cost_rate(self) -> Decimal:
        return self.fee_rate + self.slippage_rate


SCENARIOS = (
    PilotConfig(),
    PilotConfig(name="thin", participation=D("0.001")),
    PilotConfig(name="mark_low", mark_factor=D("0.9975")),
    PilotConfig(name="mark_high", mark_factor=D("1.0025")),
    PilotConfig(name="stress", slippage_rate=D("0.00145"), emergency_rate=D("0.02")),
)


@dataclass(frozen=True, slots=True)
class MinuteBar:
    open_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class DailySignal:
    allowed: bool
    transition: bool
    close: Decimal
    atr: Decimal


@dataclass
class Lot:
    stage: int
    qty: Decimal = ZERO
    entry: Decimal = ZERO
    gross: Decimal = ZERO
    fees: Decimal = ZERO
    slippage: Decimal = ZERO
    emergency: Decimal = ZERO
    funding: Decimal = ZERO
    peak_gross: Decimal = ZERO
    entered_qty: Decimal = ZERO
    exited_qty: Decimal = ZERO
    traded_notional: Decimal = ZERO

    def value(self, price: Decimal) -> Decimal:
        return self.gross + self.qty * (price - self.entry)

    def net(self, price: Decimal) -> Decimal:
        return self.value(price) - self.fees - self.slippage - self.emergency - self.funding


@dataclass
class Position:
    symbol: str
    start_ms: int
    atr: Decimal
    stop: Decimal
    lots: dict[int, Lot] = field(default_factory=dict)
    q0: Decimal = ZERO
    p0: Decimal = ZERO
    peak_close: Decimal = ZERO
    next_stage: int = 1
    initial_final: bool = False
    closing: str | None = None
    end_ms: int | None = None
    reason: str = "OPEN"

    @property
    def qty(self) -> Decimal:
        return sum((lot.qty for lot in self.lots.values()), ZERO)

    def value(self, price: Decimal) -> Decimal:
        return sum((lot.value(price) for lot in self.lots.values()), ZERO)

    def net(self, price: Decimal) -> Decimal:
        return sum((lot.net(price) for lot in self.lots.values()), ZERO)


@dataclass
class Pending:
    stage: int
    requested: Decimal
    remaining: Decimal
    atr: Decimal
    decision_ms: int
    approved: Decimal = ZERO
    filled: Decimal = ZERO
    attempts: int = 0
    reasons: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class DailyRecord:
    day_ms: int
    equity: Decimal
    exposure: Decimal
    risk: Decimal
    fees: Decimal
    slippage: Decimal
    emergency: Decimal
    funding: Decimal
    gross: Decimal
    symbol_value: Mapping[str, Decimal]


def tick(symbol: str) -> Decimal:
    return D("0.1") if symbol == "BTCUSDT" else D("0.01")


def minimum_notional(symbol: str) -> Decimal:
    return D(100) if symbol == "BTCUSDT" else D(20)


def limit_fill(
    symbol: str, bar: MinuteBar, reference: Decimal, qty: Decimal, buy: bool, cfg: PilotConfig
) -> tuple[Decimal, Decimal]:
    """Completed-minute fill proxy. Touch alone and gaps beyond cap never fill."""
    price_tick = tick(symbol)
    if buy:
        cap = floor_to_step(reference * (ONE + cfg.cap_pct), price_tick)
        price = max(bar.open, reference)
        penetrated = bar.low < price - price_tick and price <= cap
    else:
        cap = (reference * (ONE - cfg.cap_pct) / price_tick).to_integral_value(
            rounding=ROUND_CEILING
        ) * price_tick
        price = min(bar.open, reference)
        penetrated = bar.high > price + price_tick and price >= cap
    if not penetrated:
        return ZERO, price
    return min(qty, floor_to_step(bar.volume * cfg.participation, D("0.001"))), price


class PilotEngine:
    """Cash + realised PnL + unrealised linear-futures accounting, Decimal only."""

    def __init__(self, cfg: PilotConfig, adds: bool, initial_prices: Mapping[str, Decimal]) -> None:
        self.cfg = cfg
        self.adds = adds
        self.cash = cfg.capital
        self.prices = dict(initial_prices)
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, Pending] = {}
        self.finished: list[Position] = []
        self.events: list[dict[str, object]] = []
        self.fill_ledger: list[dict[str, object]] = []
        self.funding_ledger: list[dict[str, object]] = []
        self.daily: list[DailyRecord] = []
        self.reasons: Counter[str] = Counter()
        self.latest_rate = dict.fromkeys(SYMBOLS, ZERO)
        self.last_exit: dict[str, int] = {}
        self.day_start = cfg.capital
        self.day_locked = False
        self.high_water = cfg.capital
        self.minute_mdd = ZERO
        self.max_exposure = ZERO
        self.max_risk = ZERO
        self.cap_breach_minutes = 0
        self.halted = False
        self.losses = 0
        self.cooldown_until_ms = 0
        self.fees = ZERO
        self.slippage = ZERO
        self.emergency = ZERO
        self.funding = ZERO
        self.traded_notional = ZERO
        self.last_ms: int | None = None

    def equity(self, prices: Mapping[str, Decimal] | None = None) -> Decimal:
        prices = self.prices if prices is None else prices
        return self.cash + sum(
            (
                lot.qty * (prices[s] - lot.entry)
                for s, pos in self.positions.items()
                for lot in pos.lots.values()
            ),
            ZERO,
        )

    def _mark(self, symbol: str) -> Decimal:
        return self.prices[symbol] * self.cfg.mark_factor

    def _reserve(self, symbol: str) -> Decimal:
        return self._mark(symbol) * (
            2 * self.cfg.cost_rate
            + self.cfg.emergency_rate
            + max(self.latest_rate[symbol], self.cfg.funding_reserve)
        )

    def risk(self) -> Decimal:
        return sum(
            (
                pos.qty * (max(self._mark(s) - pos.stop, ZERO) + self._reserve(s))
                for s, pos in self.positions.items()
            ),
            ZERO,
        )

    def exposure(self) -> Decimal:
        return sum((pos.qty * self._mark(s) for s, pos in self.positions.items()), ZERO)

    def _veto(self, symbol: str, as_of_ms: int) -> str | None:
        if self.halted:
            return "HALTED"
        if self.day_locked or self.equity() <= self.day_start * (ONE - self.cfg.daily_loss_pct):
            return "DAILY_LOSS"
        if as_of_ms < self.cooldown_until_ms:
            return "LOSS_COOLDOWN"
        if as_of_ms < self.last_exit.get(symbol, 0) + self.cfg.reentry_ms:
            return "REENTRY_COOLDOWN"
        if symbol in self.positions and self.positions[symbol].closing is not None:
            return "CLOSING"
        return None

    def _allocate(self, intents: Mapping[str, Pending], as_of_ms: int) -> dict[str, Decimal]:
        """Shared proportional scaling; no symbol gets another's rounded remainder."""
        equity = self.equity()
        desired: dict[str, Decimal] = {}
        units: dict[str, Decimal] = {}
        for s, order in intents.items():
            reason = self._veto(s, as_of_ms)
            stop_distance = (
                2 * order.atr
                if order.stage == 0
                else max(self._mark(s) - self.positions[s].stop, ZERO)
            )
            if order.stage == 0 and stop_distance / self._mark(s) < D("0.01"):
                reason = "TIGHT_STOP"
            if (ONE / self.cfg.leverage - self.cfg.maintenance_rate) < (
                2 * stop_distance / self._mark(s)
            ):
                reason = "LIQUIDATION_BUFFER"
            if reason is not None or equity <= ZERO:
                order.reasons.add(reason or "NO_EQUITY")
                continue
            held = self.positions[s].qty if s in self.positions else ZERO
            cap = max(
                equity * self.cfg.margin_cap_pct * self.cfg.leverage / self._mark(s) - held, ZERO
            )
            desired[s] = min(order.remaining, cap)
            units[s] = stop_distance + self._reserve(s)
            if desired[s] < order.remaining:
                order.reasons.add("POSITION_MARGIN")
        new_notional = sum((q * self._mark(s) for s, q in desired.items()), ZERO)
        new_risk = sum((q * units[s] for s, q in desired.items()), ZERO)
        scale = ONE
        budgets = (
            (equity * self.cfg.risk_cap_pct - self.risk(), new_risk, "TOTAL_STOP_RISK"),
            (equity * self.cfg.gross_cap - self.exposure(), new_notional, "GROSS_CAP"),
            (
                (equity - self.exposure() / self.cfg.leverage) * self.cfg.leverage / D("1.5"),
                new_notional,
                "AVAILABLE_MARGIN",
            ),
        )
        for budget, wanted, reason in budgets:
            if wanted > ZERO and budget < wanted:
                candidate = max(budget, ZERO) / wanted
                scale = min(scale, candidate)
                for s in desired:
                    intents[s].reasons.add(reason)
        approved: dict[str, Decimal] = {}
        for s, q in desired.items():
            raw = q * scale
            rounded = floor_to_step(raw, D("0.001"))
            reason = None
            if raw <= ZERO:
                reason = "NO_BUDGET"
            elif (raw - rounded) / raw > self.cfg.max_rounding_pct:
                reason = "SIZING_RESOLUTION"
            elif intents[s].attempts == 0 and rounded * self._mark(s) < minimum_notional(s):
                reason = "BELOW_MIN_NOTIONAL"
            if reason is not None:
                intents[s].reasons.add(reason)
                continue
            approved[s] = rounded
        return approved

    def begin_day(self, day_ms: int, signals: Mapping[str, DailySignal]) -> None:
        self.day_start = self.equity()
        self.day_locked = False
        self._expire_cooldown(day_ms)
        intents: dict[str, Pending] = {}
        for s in SYMBOLS:
            signal = signals[s]
            if signal.atr <= ZERO:
                raise DataIntegrityError("missing ATR")
            if s in self.positions:
                pos = self.positions[s]
                pos.peak_close = max(pos.peak_close, signal.close)
                new_stop = max(pos.stop, pos.peak_close - 3 * pos.atr)
                if not signal.allowed or self._mark(s) <= new_stop:
                    pos.closing = "TREND_OFF" if not signal.allowed else "TRAIL_ALREADY_CROSSED"
                    if s in self.pending:
                        self._finish_order(s)
                    continue
                pos.stop = new_stop
                stage = pos.next_stage
                if stage <= 2 and signal.close >= pos.p0 + (2 if stage == 1 else 4) * pos.atr:
                    if self.adds:
                        pos.next_stage += 1
                        if stage == 2 and 1 not in pos.lots:
                            self.events.append(
                                {
                                    "symbol": s,
                                    "stage": 2,
                                    "decision_ms": day_ms,
                                    "filled": "0",
                                    "requested": str(pos.q0 / 4),
                                    "approved": "0",
                                    "reasons": ["FIRST_ADD_UNFILLED"],
                                }
                            )
                            self.reasons["FIRST_ADD_UNFILLED"] += 1
                        else:
                            qty = pos.q0 * (D("0.5") if stage == 1 else D("0.25"))
                            intents[s] = Pending(stage, qty, qty, pos.atr, day_ms)
                    else:
                        pos.next_stage += 1
                        self.reasons[f"B_DIAGNOSTIC_STAGE_{stage}"] += 1
            elif signal.transition:
                qty = self.equity() * self.cfg.initial_risk_pct / (2 * signal.atr)
                intents[s] = Pending(0, qty, qty, signal.atr, day_ms)
        approved = self._allocate(intents, day_ms)
        for s, order in intents.items():
            order.approved = approved.get(s, ZERO)
            order.remaining = order.approved
            self.pending[s] = order
            if order.approved == ZERO:
                self._finish_order(s)

    def _expire_cooldown(self, as_of_ms: int) -> None:
        if self.cooldown_until_ms and as_of_ms >= self.cooldown_until_ms:
            self.losses = 0
            self.cooldown_until_ms = 0

    def _finish_order(self, symbol: str) -> None:
        order = self.pending.pop(symbol)
        if order.filled == ZERO:
            order.reasons.add("UNFILLED")
        elif order.filled < order.requested:
            order.reasons.add("REDUCED_OR_PARTIAL")
        if order.stage == 0 and symbol in self.positions:
            pos = self.positions[symbol]
            pos.q0 = pos.lots[0].entered_qty
            pos.p0 = pos.lots[0].entry
            pos.initial_final = True
        self.events.append(
            {
                "symbol": symbol,
                "stage": order.stage,
                "decision_ms": order.decision_ms,
                "requested": str(order.requested),
                "approved": str(order.approved),
                "filled": str(order.filled),
                "attempts": order.attempts,
                "reasons": sorted(order.reasons),
            }
        )
        self.reasons.update(order.reasons)

    def pay_funding(self, symbol: str, funding_ms: int, rate: Decimal) -> None:
        self.latest_rate[symbol] = rate
        if symbol not in self.positions:
            return
        pos = self.positions[symbol]
        price = self._mark(symbol)
        for lot in pos.lots.values():
            payment = lot.qty * price * rate
            lot.funding += payment
            self.funding += payment
            self.cash -= payment
            self.funding_ledger.append(
                {
                    "symbol": symbol,
                    "stage": lot.stage,
                    "funding_ms": funding_ms,
                    "qty": str(lot.qty),
                    "proxy_mark": str(price),
                    "rate": str(rate),
                    "payment": str(payment),
                    "start_ms": pos.start_ms,
                }
            )

    def _fill(
        self,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        stage: int,
        as_of_ms: int,
        atr: Decimal,
        *,
        buy: bool,
        emergency: bool = False,
    ) -> None:
        notional = qty * price
        fee = notional * self.cfg.fee_rate
        slip = notional * self.cfg.slippage_rate
        extra = notional * self.cfg.emergency_rate if emergency else ZERO
        self.cash -= fee + slip + extra
        self.fees += fee
        self.slippage += slip
        self.emergency += extra
        self.traded_notional += notional
        if buy:
            if symbol not in self.positions:
                self.positions[symbol] = Position(
                    symbol, as_of_ms, atr, price - 2 * atr, peak_close=price
                )
            pos = self.positions[symbol]
            lot = pos.lots.setdefault(stage, Lot(stage))
            lot.entry = (lot.qty * lot.entry + qty * price) / (lot.qty + qty)
            lot.qty += qty
            lot.entered_qty += qty
            if stage == 0:
                pos.stop = max(pos.stop, lot.entry - 2 * atr)
        else:
            pos = self.positions[symbol]
            lot = pos.lots[stage]
            realised = qty * (price - lot.entry)
            self.cash += realised
            lot.gross += realised
            lot.qty -= qty
            lot.exited_qty += qty
        lot.fees += fee
        lot.slippage += slip
        lot.emergency += extra
        lot.traded_notional += notional
        self.fill_ledger.append(
            {
                "symbol": symbol,
                "stage": stage,
                "as_of_ms": as_of_ms,
                "buy": buy,
                "qty": str(qty),
                "price": str(price),
                "fee": str(fee),
                "slippage": str(slip),
                "emergency": str(extra),
                "start_ms": pos.start_ms,
            }
        )

    def _close(
        self,
        symbol: str,
        qty: Decimal,
        price: Decimal,
        as_of_ms: int,
        reason: str,
        *,
        emergency: bool = False,
    ) -> None:
        pos = self.positions[symbol]
        total = pos.qty
        quantities = [(stage, lot.qty) for stage, lot in pos.lots.items() if lot.qty > ZERO]
        # Pro-rata close, exact Decimal remainder goes to final lot, not another symbol.
        remaining = qty
        for index, (stage, lot_qty) in enumerate(quantities):
            amount = remaining if index == len(quantities) - 1 else qty * lot_qty / total
            self._fill(
                symbol, amount, price, stage, as_of_ms, pos.atr, buy=False, emergency=emergency
            )
            remaining -= amount
        if pos.qty <= D("1e-20"):
            pos.end_ms = as_of_ms
            pos.reason = reason
            self.finished.append(pos)
            del self.positions[symbol]
            self.last_exit[symbol] = as_of_ms
            if pos.net(price) < ZERO:
                self.losses += 1
                if self.losses >= self.cfg.loss_limit:
                    self.cooldown_until_ms = as_of_ms + self.cfg.cooldown_ms
            else:
                self.losses = 0
                self.cooldown_until_ms = 0

    def _stop(self, symbol: str, bar: MinuteBar) -> bool:
        if symbol not in self.positions:
            return False
        pos = self.positions[symbol]
        if bar.low * self.cfg.mark_factor > pos.stop:
            return False
        if symbol in self.pending:
            self._finish_order(symbol)
        price = min(bar.open, pos.stop / self.cfg.mark_factor)
        liq = (
            sum((lot.qty * lot.entry for lot in pos.lots.values()), ZERO)
            / pos.qty
            * (ONE - ONE / self.cfg.leverage + self.cfg.maintenance_rate)
        )
        if bar.low * self.cfg.mark_factor <= liq:
            self.reasons["LIQUIDATION_POSSIBLE_INTRAMINUTE"] += 1
        self._close(symbol, pos.qty, price, bar.open_ms + MINUTE_MS, "STOP", emergency=True)
        return True

    def step(
        self, bars: Mapping[str, MinuteBar], funding: Mapping[str, list[tuple[int, Decimal]]]
    ) -> None:
        as_of_ms = bars[SYMBOLS[0]].open_ms
        if not START_MS <= as_of_ms < END_MS:
            raise DataIntegrityError("209 forbidden evaluation boundary")
        if self.last_ms is not None and as_of_ms != self.last_ms + MINUTE_MS:
            raise DataIntegrityError("minute gap or duplicate")
        if any(bar.open_ms != as_of_ms for bar in bars.values()):
            raise DataIntegrityError("unaligned symbols")
        self.last_ms = as_of_ms
        self._expire_cooldown(as_of_ms)
        for s in SYMBOLS:
            for timestamp, rate in funding.get(s, []):
                if not as_of_ms <= timestamp < as_of_ms + MINUTE_MS:
                    raise DataIntegrityError("funding timestamp outside minute")
                self.pay_funding(s, timestamp, rate)
        if not self.positions and not self.pending:
            self.prices = {s: bars[s].close for s in SYMBOLS}
            return
        stopped = {s for s in SYMBOLS if self._stop(s, bars[s])}
        approved = self._allocate(self.pending, as_of_ms) if self.pending else {}
        for s in SYMBOLS:
            if s in stopped:
                continue
            bar = bars[s]
            if s in self.positions and self.positions[s].closing is not None:
                pos = self.positions[s]
                qty, price = limit_fill(s, bar, self.prices[s], pos.qty, False, self.cfg)
                if qty > ZERO:
                    self._close(s, qty, price, as_of_ms + MINUTE_MS, pos.closing or "CLOSE")
                continue
            if s not in self.pending:
                continue
            order = self.pending[s]
            order.attempts += 1
            qty, price = limit_fill(s, bar, self.prices[s], approved.get(s, ZERO), True, self.cfg)
            if qty > ZERO:
                self._fill(s, qty, price, order.stage, as_of_ms + MINUTE_MS, order.atr, buy=True)
                order.filled += qty
                order.remaining -= qty
            if order.remaining <= ZERO or order.attempts >= self.cfg.max_attempts:
                self._finish_order(s)
            # Unknown same-minute path: charge stop after new fill conservatively.
            self._stop(s, bar)
        low_prices = {s: bars[s].low for s in SYMBOLS}
        if self.equity(low_prices) <= self.day_start * (ONE - self.cfg.daily_loss_pct):
            self.day_locked = True
        self.prices = {s: bars[s].close for s in SYMBOLS}
        equity = self.equity()
        for s, pos in self.positions.items():
            for lot in pos.lots.values():
                lot.peak_gross = max(lot.peak_gross, lot.value(self.prices[s]))
        self.high_water = max(self.high_water, equity)
        self.minute_mdd = max(self.minute_mdd, ONE - equity / self.high_water)
        if equity <= self.high_water * (ONE - self.cfg.halt_dd_pct):
            self.halted = True
            for pos in self.positions.values():
                pos.closing = "HALTED"
        if equity > ZERO:
            self.max_exposure = max(self.max_exposure, self.exposure() / equity)
            self.max_risk = max(self.max_risk, self.risk() / equity)
            if self.risk() > equity * self.cfg.risk_cap_pct or any(
                p.qty * self._mark(s) / self.cfg.leverage > equity * self.cfg.margin_cap_pct
                for s, p in self.positions.items()
            ):
                self.cap_breach_minutes += 1

    def end_day(self, day_ms: int) -> None:
        values = dict.fromkeys(SYMBOLS, ZERO)
        for pos in [*self.finished, *self.positions.values()]:
            values[pos.symbol] += pos.net(self.prices[pos.symbol])
        gross = sum(
            (p.value(self.prices[p.symbol]) for p in [*self.finished, *self.positions.values()]),
            ZERO,
        )
        if abs(self.equity() - self.cfg.capital - sum(values.values(), ZERO)) > D("1e-15"):
            raise DataIntegrityError("accounting conservation failed")
        equity = self.equity()
        self.daily.append(
            DailyRecord(
                day_ms,
                equity,
                self.exposure() / equity,
                self.risk() / equity,
                self.fees,
                self.slippage,
                self.emergency,
                self.funding,
                gross,
                values,
            )
        )
