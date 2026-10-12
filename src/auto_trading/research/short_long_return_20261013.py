"""Frozen shared-cash attribution pilot. Not an operating strategy or risk engine."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.core.money import floor_to_step

D = Decimal
ZERO = D(0)
H4 = 14_400_000
DAY = 86_400_000
INITIAL = D(10000)
SPOT_STEP, FUT_STEP = D("0.000001"), D("0.001")
BAND, CAP, PARTICIPATION = D("0.05"), D("0.003"), D("0.001")


@dataclass(frozen=True)
class Bar:
    o: Decimal
    h: Decimal
    low: Decimal
    c: Decimal
    volume: Decimal


@dataclass(frozen=True)
class CashEvent:
    at_ms: int
    rate: Decimal
    mark: Decimal


@dataclass(frozen=True)
class Frame:
    at_ms: int
    spot: Mapping[str, Bar]
    perp: Mapping[str, Bar]
    previous_spot: Mapping[str, Bar]
    previous_perp: Mapping[str, Bar]
    targets: Mapping[str, Decimal] | None
    target_as_of_ms: int
    signals: frozenset[str]
    signal_as_of_ms: int
    funding: Mapping[str, tuple[CashEvent, ...]]


@dataclass
class Position:
    qty: Decimal
    entry: Decimal
    raw_entry: Decimal
    entry_ms: int
    opened_qty: Decimal
    gross: Decimal = ZERO
    costs: Decimal = ZERO
    funding: Decimal = ZERO


def simulate(
    frames: Sequence[Frame],
    *,
    end_ms: int,
    mode: Literal["BASE", "PRIORITY", "WAIT"],
    cost: int = 1,
    delay: int = 0,
) -> dict[str, Any]:
    """Same spot targets, each arm's equity; futures only while that coin is flat."""
    selected = [f for f in frames if f.at_ms < end_ms]
    if (
        not selected
        or mode not in ("BASE", "PRIORITY", "WAIT")
        or cost not in (1, 2)
        or delay not in (0, 1)
    ):
        raise DataIntegrityError("unregistered/empty run")
    symbols = tuple(sorted(selected[0].spot))
    cash = peak = day_start = INITIAL
    sq = dict.fromkeys(symbols, ZERO)
    goals = dict.fromkeys(symbols, ZERO)
    active: dict[str, Position] = {}
    returns_waiting: dict[str, int] = {}
    pending_rebalance: set[str] = set()
    short_closed_ms = dict.fromkeys(symbols, -DAY)
    short_halted, streak, cooldown_ms = False, 0, -1
    counts: Counter[str] = Counter()
    trades: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    delays: list[int] = []
    spot_market = short_market = spot_fee = short_fee = spot_slip = short_slip = fund = turnover = (
        ZERO
    )
    margin_alerts = 0
    worst_dd = ZERO
    sf, ss, ff, fs, stop_extra = (
        D("0.001") * cost,
        D("0.0005") * cost,
        D("0.0005") * cost,
        D("0.00045") * cost,
        D("0.002") * cost,
    )

    def equity(f: Frame, field: str) -> Decimal:
        return (
            cash
            + sum((sq[s] * getattr(f.spot[s], field) for s in symbols), ZERO)
            + sum(
                (p.qty * (p.entry - getattr(f.perp[s], field)) for s, p in active.items()),
                ZERO,
            )
        )

    def close_short(
        f: Frame, s: str, raw: Decimal, reason: str, *, emergency: bool = False
    ) -> None:
        nonlocal cash, short_market, short_fee, short_slip, turnover, streak, cooldown_ms
        p = active[s]
        px = raw * (1 + fs + (stop_extra if emergency else ZERO))
        prev = f.previous_perp[s]
        if not emergency and px > prev.c * (1 + CAP):
            counts["short_exit_limit_miss"] += 1
            return
        qty = (
            p.qty if emergency else min(p.qty, floor_to_step(prev.volume * PARTICIPATION, FUT_STEP))
        )
        if qty <= ZERO:
            counts["short_exit_liquidity_zero"] += 1
            return
        if qty < p.qty:
            counts["short_exit_partial"] += 1
        fee, slip = qty * px * ff, qty * (px - raw)
        cash += qty * (p.entry - px) - fee
        short_market += qty * (p.raw_entry - raw)
        short_fee += fee
        short_slip += slip
        turnover += qty * px
        p.gross += qty * (p.raw_entry - raw)
        p.costs += fee + slip
        p.qty -= qty
        counts[f"exit_piece:{reason}"] += 1
        if p.qty == ZERO:
            net = p.gross - p.costs + p.funding
            trades.append(
                {
                    "symbol": s,
                    "entry_ms": p.entry_ms,
                    "exit_ms": f.at_ms + (H4 if emergency else 0),
                    "reason": reason,
                    "qty": p.opened_qty,
                    "market_gross": p.gross,
                    "costs": p.costs,
                    "funding": p.funding,
                    "net": net,
                }
            )
            streak = streak + 1 if net < ZERO else 0
            if streak >= 4:
                cooldown_ms, streak = f.at_ms + H4 + 240 * 60000, 0
            del active[s]
            short_closed_ms[s] = f.at_ms + (H4 if emergency else 0)
            cooldown_ms = max(cooldown_ms, short_closed_ms[s] + 60 * 60000)
            counts["short_closed"] += 1

    previous_at: int | None = None
    for f in selected:
        if previous_at is not None and f.at_ms != previous_at + H4:
            raise DataIntegrityError("frame gap/order")
        previous_at = f.at_ms
        if f.target_as_of_ms > f.at_ms or f.signal_as_of_ms > f.at_ms:
            raise CausalityError("future decision feature")
        if f.targets is not None and (
            set(f.targets) != set(symbols)
            or any(not ZERO <= w <= D("0.5") for w in f.targets.values())
        ):
            raise DataIntegrityError("target contract")
        for mapping in (f.spot, f.perp, f.previous_spot, f.previous_perp):
            if set(mapping) != set(symbols):
                raise DataIntegrityError("symbol mismatch")
            for b in mapping.values():
                if (
                    not all(v.is_finite() for v in (b.o, b.h, b.low, b.c, b.volume))
                    or not ZERO < b.low <= min(b.o, b.c) <= max(b.o, b.c) <= b.h
                    or b.volume < ZERO
                ):
                    raise DataIntegrityError("bar integrity")
        spot_market += sum((sq[s] * (f.spot[s].o - f.previous_spot[s].c) for s in symbols), ZERO)
        if f.at_ms % DAY == 0:
            day_start = equity(f, "o")
        if f.targets is not None:
            goals = dict(f.targets)
        # Exact-boundary funding belongs to carried positions, never fresh entries.
        for s, p in list(active.items()):
            gap_stop = f.perp[s].o >= p.entry * D("1.03")
            for event in f.funding[s]:
                if event.at_ms == f.at_ms:
                    amount = p.qty * event.rate * event.mark
                    if gap_stop:
                        amount = min(ZERO, amount)
                    cash += amount
                    p.funding += amount
                    fund += amount
            if gap_stop:
                close_short(f, s, f.perp[s].o, "STOP_GAP", emergency=True)
        for s in symbols:
            if goals[s] == ZERO and s in returns_waiting:
                counts["return_canceled_before_buy"] += 1
                del returns_waiting[s]
            if (
                goals[s] > ZERO
                and (s in active or f.at_ms < short_closed_ms[s] + 60 * 60000)
                and s not in returns_waiting
            ):
                returns_waiting[s] = f.at_ms
                counts["long_return_requests"] += 1
            if s in active:
                p = active[s]
                if (
                    mode == "PRIORITY"
                    and s in returns_waiting
                    and f.at_ms >= returns_waiting[s] + delay * H4
                ):
                    close_short(f, s, f.perp[s].o, "LONG_PRIORITY")
                elif f.at_ms >= p.entry_ms + 48 * 3600000:
                    close_short(f, s, f.perp[s].o, "48H")
        # Daily rebalance or catch-up after a short prevented an earlier scheduled return.
        for s in symbols:
            if f.targets is None and s not in returns_waiting and s not in pending_rebalance:
                continue
            if goals[s] > ZERO and (s in active or f.at_ms < short_closed_ms[s] + 60 * 60000):
                counts["blocked_long_bars"] += 1
                continue
            e = equity(f, "o")
            if e <= ZERO:
                continue
            current = sq[s] * f.spot[s].o / e
            if sq[s] > ZERO and goals[s] > ZERO and abs(goals[s] - current) < BAND:
                pending_rebalance.discard(s)
                continue
            want = floor_to_step(goals[s] * e / f.spot[s].o, SPOT_STEP)
            change = want - sq[s]
            if change == ZERO:
                pending_rebalance.discard(s)
                continue
            buying = change > ZERO
            px = f.spot[s].o * (1 + ss if buying else 1 - ss)
            reference = f.previous_spot[s].c
            if (buying and px > reference * (1 + CAP)) or (
                not buying and px < reference * (1 - CAP)
            ):
                counts["spot_limit_miss"] += 1
                pending_rebalance.add(s)
                continue
            qty = min(
                abs(change), floor_to_step(f.previous_spot[s].volume * PARTICIPATION, SPOT_STEP)
            )
            if buying:
                reserve = sum((p.qty * f.perp[n].o * D("1.5") for n, p in active.items()), ZERO)
                qty = min(
                    qty, floor_to_step(max(ZERO, cash - reserve) / (px * (1 + sf)), SPOT_STEP)
                )
            if qty <= ZERO:
                counts["spot_size_zero"] += 1
                pending_rebalance.add(s)
                continue
            fee, slip = qty * px * sf, qty * abs(px - f.spot[s].o)
            cash -= qty * px + fee if buying else -qty * px + fee
            sq[s] += qty if buying else -qty
            spot_fee += fee
            spot_slip += slip
            turnover += qty * px
            counts["spot_fills"] += 1
            if qty < abs(change):
                pending_rebalance.add(s)
                counts["spot_partial"] += 1
            else:
                pending_rebalance.discard(s)
            if buying and s in returns_waiting:
                delays.append(f.at_ms - returns_waiting.pop(s))
                counts["long_return_completed"] += 1
        if mode != "BASE":
            for s in sorted(f.signals):
                if s in active:
                    counts["short_overlap_skipped"] += 1
                    continue
                if goals[s] > ZERO or sq[s] > ZERO:
                    counts["short_long_conflict"] += 1
                    continue
                e = equity(f, "o")
                if short_halted or e <= ZERO or e <= day_start * D("0.98") or f.at_ms < cooldown_ms:
                    counts["short_risk_veto"] += 1
                    continue
                raw, reference = f.perp[s].o, f.previous_perp[s].c
                px = raw * (1 - fs)
                if px < reference * (1 - CAP):
                    counts["short_entry_limit_miss"] += 1
                    continue
                gross = sum((sq[n] * f.spot[n].o for n in symbols), ZERO) + sum(
                    (p.qty * f.perp[n].o for n, p in active.items()), ZERO
                )
                budget = D("0.03") + (stop_extra + fs) * D("1.03") + ff * D("2.03")
                existing = sum((p.qty * f.perp[n].o for n, p in active.items()), ZERO)
                notional = min(
                    e * D("0.25"),
                    e * D("0.01") / budget,
                    max(ZERO, e - gross),
                    max(ZERO, cash / D("1.5") - existing),
                )
                qty = floor_to_step(
                    min(notional / px, f.previous_perp[s].volume * PARTICIPATION), FUT_STEP
                )
                if qty <= ZERO:
                    counts["short_size_zero"] += 1
                    continue
                fee, slip = qty * px * ff, qty * (raw - px)
                cash -= fee
                short_fee += fee
                short_slip += slip
                turnover += qty * px
                active[s] = Position(qty, px, raw, f.at_ms, qty, costs=fee + slip)
                counts["short_opened"] += 1
        worst = (
            cash
            + sum((sq[s] * f.spot[s].low for s in symbols), ZERO)
            + sum((p.qty * (p.entry - f.perp[s].h) for s, p in active.items()), ZERO)
        )
        worst_dd = max(worst_dd, 1 - worst / peak)
        if worst <= peak * D("0.70") and not short_halted:
            short_halted = True
            counts["short_dd_halt"] += 1
        worst_wallet = cash + sum(
            (p.qty * (p.entry - f.perp[s].h) for s, p in active.items()), ZERO
        )
        gross_short = sum((p.qty * f.perp[s].h for s, p in active.items()), ZERO)
        if gross_short > ZERO and worst_wallet <= gross_short * D("0.005"):
            margin_alerts += 1
        for s, p in list(active.items()):
            stopped = f.perp[s].h >= p.entry * D("1.03")
            for event in f.funding[s]:
                if f.at_ms < event.at_ms < f.at_ms + H4:
                    amount = p.qty * event.rate * event.mark
                    if stopped:
                        amount = min(ZERO, amount)
                    cash += amount
                    fund += amount
                    p.funding += amount
            if stopped:
                close_short(f, s, max(f.perp[s].o, p.entry * D("1.03")), "STOP", emergency=True)
        spot_market += sum((sq[s] * (f.spot[s].c - f.spot[s].o) for s in symbols), ZERO)
        e = equity(f, "c")
        peak = max(peak, e)
        gross = sum((sq[s] * f.spot[s].c for s in symbols), ZERO) + sum(
            (p.qty * f.perp[s].c for s, p in active.items()), ZERO
        )
        trace.append(
            {
                "at_ms": f.at_ms,
                "equity": e,
                "spot_qty": dict(sq),
                "blocked": {
                    s: goals[s] > ZERO
                    and (s in active or f.at_ms < short_closed_ms[s] + 60 * 60000)
                    for s in symbols
                },
                "exposure": gross / e if e > ZERO else ZERO,
            }
        )
    # Common explicit terminal IOC proxy; no unbounded forced market fill.
    f = selected[-1]
    for s in list(active):
        terminal = Frame(
            f.at_ms,
            f.spot,
            f.perp,
            f.previous_spot,
            {n: Bar(b.c, b.c, b.c, b.c, f.previous_perp[n].volume) for n, b in f.perp.items()},
            None,
            -1,
            frozenset(),
            -1,
            f.funding,
        )
        close_short(terminal, s, f.perp[s].c, "TERMINAL")
    for s in symbols:
        qty = min(sq[s], floor_to_step(f.previous_spot[s].volume * PARTICIPATION, SPOT_STEP))
        px = f.spot[s].c * (1 - ss)
        fee, slip = qty * px * sf, qty * f.spot[s].c * ss
        cash += qty * px - fee
        sq[s] -= qty
        spot_fee += fee
        spot_slip += slip
        turnover += qty * px
    reserve = sum((sq[s] * f.spot[s].c * sf for s in symbols), ZERO) + sum(
        (p.qty * f.perp[s].c * ff for s, p in active.items()), ZERO
    )
    end_equity = equity(f, "c") - reserve
    market_short = short_market + sum(
        (p.qty * (p.raw_entry - f.perp[s].c) for s, p in active.items()), ZERO
    )
    residual = (
        end_equity
        - INITIAL
        - spot_market
        - market_short
        - fund
        + spot_fee
        + short_fee
        + spot_slip
        + short_slip
        + reserve
    )
    trace[-1]["equity"] = end_equity
    return {
        "trace": trace,
        "trades": trades,
        "counts": dict(counts),
        "spot_market": spot_market,
        "short_market": market_short,
        "spot_fee": spot_fee,
        "short_fee": short_fee,
        "spot_slip": spot_slip,
        "short_slip": short_slip,
        "funding": fund,
        "turnover": turnover,
        "net": end_equity - INITIAL,
        "residual": residual,
        "terminal_open": len(active) + sum(q > ZERO for q in sq.values()),
        "fee_reserve": reserve,
        "return_delays_ms": delays,
        "return_pending": len(returns_waiting),
        "worst_dd_bound": worst_dd,
        "margin_alerts": margin_alerts,
    }
